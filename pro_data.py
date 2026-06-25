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
    return datetime.date.today().year - 1


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
