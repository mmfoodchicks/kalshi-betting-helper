"""Recent usage folded into Sleeper's weekly lines: who has actually been
getting the ball.

The DFS engine and the app's lineup sheet share a team's targets and carries
by Sleeper's projected lines. research/nfl_usage_validation.py asked whether a
player's recent share of his team's targets and carries carries anything
Sleeper's line does not, by a rule written before any number was computed
(2026-10-18 13:30 UTC): candidates chosen on 2024, tested on 2025, confirmed
on 2026 weeks 1-5, the loss the multinomial log loss of who actually got the
ball (the engine deals by a multinomial on these shares).

  targets: target share over the player's last 6 games, weight 0.3 -- 2025
           -0.00487 +/- 0.00112 per team-game against Sleeper alone (4.4 SE);
           2026 +0.00033 +/- 0.00186, flat (not a veto by the rule, and the
           early weeks hold at most four games of history).
  carries: carry share over the last 3 games, weight 0.3 -- 2025 -0.00318
           +/- 0.00146 (2.2 SE); 2026 -0.00258 +/- 0.00200, the same way.
  Air yards (nflverse's WOPR) lost to plain target share in every year, so
  nothing here reads nflverse. Report: research/reports/nfl_usage.md.

What it changes, exactly as the rule wrote it: within each team, the players
with a positive projection of the component split it q = 0.7 s + 0.3 t, s
Sleeper's share and t the trailing allocation (players with at least two
games for this team this season split the share Sleeper gives THEM by their
recent share of the team's total, floored at 0.005; the rest keep Sleeper's).
Each player's count moves to q x the team's projected total and what hangs
off it moves by the same factor -- receptions, receiving yards and touchdowns
with targets (his catch rate, yards and touchdowns per catch stay Sleeper's),
rushing yards and touchdowns with carries. Team totals stay Sleeper's, so the
engine's anchors do not move; only who gets the ball. `proj_pts` moves by the
same lines' PPR change, because the legacy simulator pins its mean to it.

The trailing games are Sleeper's own weekly box scores (the feed dfslog
grades from), kept compact in the deep store a week at a time: a week two or
more behind the board's is final and fetched once, the last one refetched
every six hours (stat corrections, a Monday game finishing). Weeks 1 and 2
have no player with two games, so nothing is fetched or changed. Any week that
cannot be read leaves the whole board on Sleeper's lines, never a partial
history.
"""
import time

import errlog

TGT_W, TGT_N = 0.3, 6
# Shipped on the Sunday of 2026 week 6, hours before its main slate locked:
# the week's boards and the owner's lineups were already built on Sleeper's
# lines, so the change starts with week 7 rather than moving a slate mid-week.
FIRST = (2026, 7)
CAR_W, CAR_N = 0.3, 3
MIN_G = 2
FLOOR = 0.005
KEY = "nfl_usage_stats_{season}_w{week:02d}"
REFRESH_S = 6 * 3600
_STATS = ("https://api.sleeper.com/stats/nfl/{season}/{week}?season_type=regular"
          "&position[]=QB&position[]=RB&position[]=WR&position[]=TE")
# component -> (weight, games, the means that move with it)
_COMPONENTS = {"rec_tgt": (TGT_W, TGT_N, ("rec_tgt", "rec", "rec_yd", "rec_td")),
               "rush_att": (CAR_W, CAR_N, ("rush_att", "rush_yd", "rush_td"))}
_PPR = {"rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0, "rush_yd": 0.1, "rush_td": 6.0}


def compact(rows):
    """One week of Sleeper's box scores as {"pl": {player_id: [team, rec_tgt,
    rush_att]} for the men who played, "tot": {team: [rec_tgt, rush_att]}}.
    The team totals are every row of the team (a player listed with no game
    adds nothing)."""
    pl, tot = {}, {}
    for r in rows or []:
        st = r.get("stats") or {}
        team = r.get("team")
        pid = str(r.get("player_id") or "")
        if not team or not pid:
            continue
        tg, ra = float(st.get("rec_tgt") or 0.0), float(st.get("rush_att") or 0.0)
        t = tot.setdefault(team, [0.0, 0.0])
        t[0] += tg
        t[1] += ra
        if float(st.get("gp") or 0.0) > 0:
            pl[pid] = [team, tg, ra]
    return {"pl": pl, "tot": tot}


def history(season, week, fetch=None, now=None):
    """{week: compact week} for every week before `week`, or None when any
    week cannot be read (stored or fetched)."""
    import deep_cache
    now = time.time() if now is None else now
    if fetch is None:
        import nfl_dfs_sim
        fetch = nfl_dfs_sim._get
    out = {}
    for wk in range(1, int(week)):
        key = KEY.format(season=int(season), week=wk)
        got, ts = deep_cache.load(key)
        stale = got is None or (wk == int(week) - 1 and (ts is None or now - ts > REFRESH_S))
        if stale:
            try:
                got = compact(fetch(_STATS.format(season=int(season), week=wk)))
                deep_cache.save(key, got)
            except Exception as e:
                errlog.note("USAGE-fetch", e)
                if got is None:
                    return None
        out[wk] = got
    return out


def trailing(pid, team, comp, n, hist):
    """His share of the team's `comp` over his last `n` games for this team,
    a ratio of sums, floored; None under MIN_G games."""
    ix = 1 if comp == "rec_tgt" else 2
    games = [w for w in sorted(hist, reverse=True)
             if (hist[w]["pl"].get(pid) or [None])[0] == team][:n]
    if len(games) < MIN_G:
        return None
    den = sum(hist[w]["tot"].get(team, [0.0, 0.0])[ix - 1] for w in games)
    if den <= 0:
        return None
    return max(FLOOR, sum(hist[w]["pl"][pid][ix] for w in games) / den)


def shares(s, r, w):
    """q = (1 - w) s + w t, t the trailing allocation (the rule's)."""
    have = [i for i, x in enumerate(r) if x is not None]
    t = list(s)
    if have:
        mass = sum(s[i] for i in have)
        rs = sum(r[i] for i in have)
        for i in have:
            t[i] = mass * r[i] / rs
    return [(1 - w) * a + w * b for a, b in zip(s, t)]


def adjust(pairs, hist):
    """Move every player's lines in place. `pairs` is [(player_id, player
    dict with team, means, proj_pts)]. Returns the summary the board stamps."""
    teams = {}
    for pid, p in pairs:
        teams.setdefault(p.get("team"), []).append((str(pid or ""), p))
    moved = 0
    shift = 0.0
    for team, members in teams.items():
        for comp, (w, n, carried) in _COMPONENTS.items():
            st = [(pid, p) for pid, p in members if float((p.get("means") or {}).get(comp) or 0.0) > 0]
            if len(st) < 2:
                continue
            proj = [float(p["means"][comp]) for _pid, p in st]
            total = sum(proj)
            s = [x / total for x in proj]
            r = [trailing(pid, team, comp, n, hist) if pid else None for pid, _p in st]
            if all(x is None for x in r):
                continue
            q = shares(s, r, w)
            for (pid, p), q_i, x in zip(st, q, proj):
                f = q_i * total / x
                if abs(f - 1.0) < 1e-12:
                    continue
                m = p["means"]
                d_pts = 0.0
                for k in carried:
                    old = float(m.get(k) or 0.0)
                    m[k] = old * f
                    d_pts += _PPR.get(k, 0.0) * (m[k] - old)
                if p.get("proj_pts") is not None:
                    p["proj_pts"] = round(float(p["proj_pts"]) + d_pts, 2)
                moved += 1
                shift += abs(d_pts)
    return {"players_moved": moved, "mean_abs_ppr_shift": round(shift / moved, 3) if moved else 0.0}


def apply(pairs, season, week, fetch=None):
    """The weekly board's hook: Sleeper's lines with recent usage folded in,
    or untouched (None) in weeks 1-2, before FIRST, and whenever the history
    cannot be read."""
    if int(week) <= MIN_G or (int(season), int(week)) < FIRST:
        return None
    hist = history(season, week, fetch=fetch)
    if not hist:
        return None
    return adjust(pairs, hist)


def stamp():
    """What a board built on these lines records."""
    return {"targets": {"weight": TGT_W, "games": TGT_N}, "carries": {"weight": CAR_W, "games": CAR_N},
            "min_games": MIN_G, "floor": FLOOR, "from": f"{FIRST[0]} week {FIRST[1]}",
            "validation": "research/reports/nfl_usage.md (rule pre-registered; 2024 select, 2025 test, 2026 confirm)"}
