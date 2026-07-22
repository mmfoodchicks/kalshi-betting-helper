"""Roster + standings + schedule feeds for the pro leagues (NFL / NBA / NHL).

Everything here comes from ESPN's public JSON. It is deliberately league-generic:
the three leagues share the same endpoint shapes, so one set of fetchers driven
by a per-league config (`LEAGUES`) serves all of them.

What we pull, per team:
  * prior-season record + point/goal differential  -> the strength backbone,
  * the current roster, partitioned by ESPN into active / injuredReserveOrOut /
    suspended -> a live availability signal (a star landing in the suspended or
    IR bucket docks the team automatically on the next refresh), plus each
    player's position, experience (0 yrs = rookie) and college,
  * the upcoming-season schedule -> the games we Monte-Carlo.

Because we re-fetch on every sim run (cached only a few hours), trades, trouble
and injuries flow through as soon as ESPN reflects them on the roster.
"""

import concurrent.futures as _cf
import clock

import racing                     # reuse its disk-cache + JSON GET helpers


# Position importance when a player is unavailable (suspended / IR). QB dwarfs
# everything in the NFL; the others are "loss of a key starter" weights. Anything
# not listed is treated as depth (small weight).
_NFL_POS_W = {"QB": 1.00, "WR": 0.34, "RB": 0.30, "CB": 0.30, "LT": 0.28,
              "OT": 0.24, "EDGE": 0.30, "DE": 0.26, "TE": 0.22, "S": 0.22,
              "DT": 0.22, "LB": 0.20, "G": 0.16, "C": 0.16}
_NBA_POS_W = {"PG": 0.6, "SG": 0.55, "SF": 0.55, "PF": 0.5, "C": 0.5, "G": 0.55,
              "F": 0.5}
_NHL_POS_W = {"G": 0.9, "C": 0.45, "LW": 0.4, "RW": 0.4, "D": 0.4, "F": 0.4}

LEAGUES = {
    "nfl": {"sport": "football", "league": "nfl", "label": "🏈 NFL",
            "games": 17, "pos_w": _NFL_POS_W, "key_pos": "QB"},
    "nba": {"sport": "basketball", "league": "nba", "label": "🏀 NBA",
            "games": 82, "pos_w": _NBA_POS_W, "key_pos": None},
    "nhl": {"sport": "hockey", "league": "nhl", "label": "🏒 NHL",
            "games": 82, "pos_w": _NHL_POS_W, "key_pos": "G"},
    "wnba": {"sport": "basketball", "league": "wnba", "label": "🏀 WNBA",
             "games": 44, "pos_w": _NBA_POS_W, "key_pos": None},
}

_SITE = "https://site.api.espn.com/apis/site/v2/sports"
_WEB = "https://site.web.api.espn.com/apis/v2/sports"


def _get(url):
    return racing._get_json(url)


def teams(league):
    """[{id, name, abbrev, location}] for the league."""
    cfg = LEAGUES[league]

    def build():
        d = _get(f"{_SITE}/{cfg['sport']}/{cfg['league']}/teams")
        out = []
        for t in d["sports"][0]["leagues"][0]["teams"]:
            tm = t["team"]
            out.append({"id": tm["id"], "name": tm["displayName"],
                        "abbrev": tm.get("abbreviation"),
                        "location": tm.get("location"),
                        "nick": tm.get("name")})
        return out
    return racing._cached(("pro_teams", league), 24 * 3600, build) or []


def standings(league, season):
    """{team_id: {wins, losses, diff_pg, games}} from a completed season."""
    cfg = LEAGUES[league]

    def build():
        d = _get(f"{_WEB}/{cfg['sport']}/{cfg['league']}/standings?season={season}")
        out = {}
        for grp in d.get("children", []):                 # conferences
            for e in grp.get("standings", {}).get("entries", []):
                st = {s["name"]: s.get("value") for s in e.get("stats", [])}
                w = st.get("wins") or 0
                l = st.get("losses") or 0
                ot = st.get("otlosses") or 0              # NHL: OT/SO losses
                g = w + l + ot or cfg["games"]
                diff = st.get("pointDifferential")
                if diff is None:
                    diff = (st.get("pointsFor") or 0) - (st.get("pointsAgainst") or 0)
                out[e["team"]["id"]] = {"wins": int(w), "losses": int(l + ot),
                                        "diff_pg": diff / g if g else 0.0, "games": int(g)}
        return out
    return racing._cached(("pro_standings", league, season), 12 * 3600, build) or {}


def groups(league):
    """{team_id: {conf, division}} so we can seed divisions + conferences."""
    cfg = LEAGUES[league]

    def build():
        # The standings tree is conf -> (division ->) entries.
        d = _get(f"{_WEB}/{cfg['sport']}/{cfg['league']}/standings?season={_recent_season(league)}"
                 "&level=3")
        out = {}
        for conf in d.get("children", []):
            cname = conf.get("name", "")
            kids = conf.get("children") or [conf]
            for div in kids:
                dname = div.get("name", cname)
                for e in div.get("standings", {}).get("entries", []):
                    out[e["team"]["id"]] = {"conf": cname, "division": dname}
        return out
    return racing._cached(("pro_groups", league), 24 * 3600, build) or {}


def _recent_season(league):
    import datetime
    return clock.today_et().year - 1


def roster_avail(league, team_id):
    """Availability signal for one team:
       {out_index, key_out (bool), out_list:[{name,pos,reason}], rookies:[...]}.
    out_index is the position-weighted sum of unavailable players (suspended or
    on IR); key_out flags the league's marquee position (NFL QB / NHL goalie)
    being among them."""
    cfg = LEAGUES[league]
    pos_w = cfg["pos_w"]

    def build():
        try:
            r = _get(f"{_SITE}/{cfg['sport']}/{cfg['league']}/teams/{team_id}/roster")
        except Exception:
            return {"out_index": 0.0, "key_out": False, "out_list": [], "rookies": []}
        out_index = 0.0
        key_out = False
        out_list, rookies = [], []
        for grp in r.get("athletes", []):
            bucket = grp.get("position")            # offense/defense/.../suspended
            for p in grp.get("items", []):
                pos = ((p.get("position") or {}).get("abbreviation") or "").upper()
                if bucket in ("suspended", "injuredReserveOrOut"):
                    w = pos_w.get(pos, 0.08)
                    out_index += w
                    if cfg["key_pos"] and pos == cfg["key_pos"]:
                        key_out = True
                    out_list.append({"name": p.get("displayName"), "pos": pos,
                                     "reason": "suspended" if bucket == "suspended" else "injured"})
                if (p.get("experience") or {}).get("years") == 0:
                    rookies.append({"name": p.get("displayName"), "pos": pos,
                                    "college": (p.get("college") or {}).get("name")})
        return {"out_index": round(out_index, 2), "key_out": key_out,
                "out_list": out_list[:12], "rookies": rookies[:12]}
    return racing._cached(("pro_avail", league, team_id), 6 * 3600, build) or \
        {"out_index": 0.0, "key_out": False, "out_list": [], "rookies": []}


def schedule(league, season):
    """Upcoming-season games as [(home_id, away_id)] across all teams (de-duped)."""
    cfg = LEAGUES[league]

    def build():
        tids = [t["id"] for t in teams(league)]

        def one(tid):
            try:
                d = _get(f"{_SITE}/{cfg['sport']}/{cfg['league']}/teams/{tid}/schedule?season={season}")
            except Exception:
                return []
            games = []
            for ev in d.get("events", []):
                if (ev.get("seasonType") or {}).get("type") != 2:   # regular season only
                    continue
                comp = (ev.get("competitions") or [{}])[0]
                home = away = None
                for c in comp.get("competitors", []):
                    tid2 = c.get("id") or (c.get("team") or {}).get("id")
                    if c.get("homeAway") == "home":
                        home = tid2
                    else:
                        away = tid2
                if home and away:
                    games.append((home, away))
            return games
        # Every game is listed by both teams, so an ordered (home,away) matchup
        # appears twice per actual meeting -> halve the counts to recover the slate.
        from collections import Counter
        cnt = Counter()
        with _cf.ThreadPoolExecutor(max_workers=8) as ex:
            for games in ex.map(one, tids):
                cnt.update(games)
        sched = []
        for (h, a), c in cnt.items():
            for _ in range((c + 1) // 2):
                sched.append((h, a))
        return sched
    return racing._cached(("pro_sched", league, season), 24 * 3600, build) or []


def season_state(league, season):
    """In-season snapshot for the deep play-by-play season sim:

        {"remaining": [(home_id, away_id), ...],   # games not yet played
         "base_wins": {team_id: wins},             # games already won (locked)
         "base_otl":  {team_id: ot/so losses},     # NHL points: OTL = 1 point
         "played":    n_completed}

    Every scheduled game is read from ESPN with its live status and score, so a
    completed game is LOCKED to its real winner (it never gets re-simulated) and
    only the unplayed remainder feeds the Monte Carlo — exactly how the MLB deep
    engine treats the season. Preseason (nothing final yet) this returns the full
    schedule as `remaining` with empty base tallies."""
    cfg = LEAGUES[league]

    def build():
        tids = [t["id"] for t in teams(league)]

        def one(tid):
            try:
                d = _get(f"{_SITE}/{cfg['sport']}/{cfg['league']}/teams/{tid}/schedule?season={season}")
            except Exception:
                return []
            out = []
            for ev in d.get("events", []):
                if (ev.get("seasonType") or {}).get("type") != 2:   # regular season only
                    continue
                comp = (ev.get("competitions") or [{}])[0]
                state = (((comp.get("status") or {}).get("type")) or {}).get("state")
                home = away = None
                hs = as_ = None
                ot = False
                for c in comp.get("competitors", []):
                    tid2 = c.get("id") or (c.get("team") or {}).get("id")
                    try:
                        sc = float((c.get("score") or {}).get("value")
                                   if isinstance(c.get("score"), dict) else c.get("score"))
                    except (TypeError, ValueError):
                        sc = None
                    if c.get("homeAway") == "home":
                        home, hs = tid2, sc
                    else:
                        away, as_ = tid2, sc
                # OT/SO flag for hockey standings points (period > 3).
                try:
                    per = int(((comp.get("status") or {}).get("period")) or 0)
                    ot = per > 3
                except (TypeError, ValueError):
                    ot = False
                if home and away:
                    out.append({"id": ev.get("id"), "home": home, "away": away,
                                "final": state == "post", "hs": hs, "as": as_, "ot": ot})
            return out

        seen = {}
        with _cf.ThreadPoolExecutor(max_workers=8) as ex:
            for games in ex.map(one, tids):
                for g in games:
                    if g["id"]:
                        seen[g["id"]] = g          # event id de-dups the two listings
        remaining, base_wins, base_otl, played = [], {}, {}, 0
        tset = set(tids)
        for g in seen.values():
            h, a = g["home"], g["away"]
            if h not in tset or a not in tset:
                continue
            if g["final"] and g["hs"] is not None and g["as"] is not None:
                played += 1
                w, l = (h, a) if g["hs"] > g["as"] else (a, h)
                base_wins[w] = base_wins.get(w, 0) + 1
                if g["ot"]:                          # loser banks an OT/SO point
                    base_otl[l] = base_otl.get(l, 0) + 1
            elif not g["final"]:
                remaining.append((h, a))
        return {"remaining": remaining, "base_wins": base_wins,
                "base_otl": base_otl, "played": played}
    # In-season this must refresh often enough to lock last night's finals.
    return racing._cached(("pro_state", league, season), 3 * 3600, build) or \
        {"remaining": [], "base_wins": {}, "base_otl": {}, "played": 0}


# ---- NFL quarterback layer ---------------------------------------------------
# The single biggest thing last-season point differential misses is the
# quarterback CHANGING: the differential embeds the old QB, and the regression
# knob assumes everything reverts alike (elite QBs don't). We identify each
# team's projected starter from the CURRENT roster, score his prior-season
# passing efficiency (wherever he played), compare it to the passer whose
# production is actually baked into the team's differential, and hand pro_sim a
# margin adjustment.

_CORE_NFL = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"


def _nfl_passing_line(pid, season):
    """{att, yds, td, int} for one athlete-season, or None."""
    try:
        s = _get(f"{_CORE_NFL}/seasons/{season}/types/2/athletes/{pid}/statistics?lang=en")
    except Exception:
        return None
    want = {"passingAttempts": "att", "passingYards": "yds",
            "passingTouchdowns": "td", "interceptions": "int"}
    out = {}
    for c in (s.get("splits", {}) or {}).get("categories", []):
        if c.get("name") != "passing":
            continue
        for st in c.get("stats", []):
            k = want.get(st.get("name"))
            if k:
                try:
                    out[k] = float(st.get("value") or 0)
                except (TypeError, ValueError):
                    pass
    return out if out.get("att") else None


def _qb_score(line):
    """ANY/A-lite: yards +20/TD −45/INT per attempt. League average ~6.2; elite
    ~7.5+; replacement ~5. The scale pro_sim converts to margin points."""
    att = line.get("att") or 0
    if att < 1:
        return None
    return (line.get("yds", 0) + 20 * line.get("td", 0) - 45 * line.get("int", 0)) / att


def nfl_qb_map(prior_season):
    """{team_id: {starter, score, att, prev_name, prev_score, changed, adj}} for
    every NFL team. `adj` is the margin-point QB adjustment pro_sim applies:
    1.5x the (new starter − departed passer) swing + 0.5x the elite-persistence
    term (new starter vs league average, restoring signal the differential
    regression washed out). Self-normalizing: the league average comes from the
    32 projected starters themselves. Best-effort per team; failures are 0-adj."""
    cfg = LEAGUES["nfl"]

    def build():
        tids = [t["id"] for t in teams("nfl")]

        def team_qbs(tid):
            """Current-roster QB candidates (healthy) + last season's primary passer pid."""
            qbs, prev_pid = [], None
            try:
                r = _get(f"{_SITE}/{cfg['sport']}/{cfg['league']}/teams/{tid}/roster")
                for grp in r.get("athletes", []):
                    if grp.get("position") in ("suspended", "injuredReserveOrOut"):
                        continue
                    for p in grp.get("items", []):
                        pos = ((p.get("position") or {}).get("abbreviation") or "").upper()
                        if pos == "QB" and p.get("id"):
                            qbs.append({"pid": str(p["id"]), "name": p.get("displayName"),
                                        "exp": (p.get("experience") or {}).get("years", 0)})
            except Exception:
                pass
            try:
                d = _get(f"{_CORE_NFL}/seasons/{prior_season}/types/2/teams/{tid}/leaders?lang=en")
                for c in d.get("categories", []):
                    if c.get("name") == "passingLeader":
                        ref = (c.get("leaders") or [{}])[0].get("athlete", {}).get("$ref", "")
                        if "/athletes/" in ref:
                            prev_pid = ref.split("/athletes/")[-1].split("?")[0]
                        break
            except Exception:
                pass
            return tid, qbs, prev_pid

        with _cf.ThreadPoolExecutor(max_workers=8) as ex:
            rooms = list(ex.map(team_qbs, tids))

        # Fetch prior-season passing lines for every candidate + previous passer.
        pids = set()
        for _tid, qbs, prev_pid in rooms:
            pids.update(q["pid"] for q in qbs)
            if prev_pid:
                pids.add(prev_pid)
        lines = {}
        with _cf.ThreadPoolExecutor(max_workers=10) as ex:
            for pid, ln in zip(pids, ex.map(lambda p: _nfl_passing_line(p, prior_season), pids)):
                lines[pid] = ln

        # Projected starter = the healthy QB with the most prior-season attempts
        # (free agency/trades counted — his stats follow him). No 100+ attempt
        # arm on the roster -> rookie/unproven starter.
        raw = {}
        for tid, qbs, prev_pid in rooms:
            best, best_att = None, 0.0
            for q in qbs:
                ln = lines.get(q["pid"])
                att = (ln or {}).get("att", 0)
                if att > best_att:
                    best, best_att = q, att
            starter = best or (qbs[0] if qbs else None)
            s_line = lines.get(starter["pid"]) if starter else None
            prev_line = lines.get(prev_pid) if prev_pid else None
            raw[tid] = {"starter": starter["name"] if starter else None,
                        "starter_pid": starter["pid"] if starter else None,
                        "att": best_att,
                        "score": _qb_score(s_line) if s_line else None,
                        "prev_pid": prev_pid,
                        "prev_score": _qb_score(prev_line) if prev_line else None}

        # Self-normalized league average over proven starters.
        proven = [r["score"] for r in raw.values() if r["score"] is not None and r["att"] >= 150]
        avg = sum(proven) / len(proven) if proven else 6.2

        def _unproven_prior(pid):
            """Draft capital sets the prior for a starter with no NFL sample —
            historical rookie efficiency tracks draft slot: a top-5 pick projects
            near-average, a Day-3 flier well below."""
            try:
                d = _get(f"{_CORE_NFL}/seasons/{prior_season + 1}/athletes/{pid}?lang=en")
                dr = d.get("draft") or {}
                sel, rnd = dr.get("selection"), dr.get("round")
            except Exception:
                sel = rnd = None
            if sel and sel <= 5:
                return avg - 0.25
            if sel and sel <= 15:
                return avg - 0.45
            if rnd == 1:
                return avg - 0.60
            if rnd in (2, 3):
                return avg - 0.85
            return avg - 1.10                    # Day 3 / undrafted / unknown

        out = {}
        for tid, r in raw.items():
            if r["score"] is not None and r["att"] >= 100:
                now = r["score"]
            else:
                now = _unproven_prior(r["starter_pid"]) if r["starter_pid"] else avg - 0.9
            prev = r["prev_score"] if r["prev_score"] is not None else avg
            changed = bool(r["starter_pid"] and r["prev_pid"]
                           and r["starter_pid"] != r["prev_pid"])
            swing = (now - prev) if changed else 0.0
            adj = max(-5.0, min(5.0, 1.5 * swing + 0.5 * (now - avg)))
            out[tid] = {"starter": r["starter"], "score": round(now, 2),
                        "att": int(r["att"]), "prev_score": round(prev, 2),
                        "changed": changed, "adj": round(adj, 2),
                        "unproven": r["score"] is None or r["att"] < 100,
                        "lg_avg": round(avg, 2)}
        return out
    return racing._cached(("nfl_qb_map", prior_season), 6 * 3600, build) or {}
