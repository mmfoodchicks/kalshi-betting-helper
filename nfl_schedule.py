"""NFL schedule: bye weeks + per-week opponents for the upcoming season.

Best-ball is a season-long, set-and-forget format, so the schedule matters in two
ways the draft can control: bye weeks (if your startable players at a position all
sit the same week, that's a guaranteed dead spot in your auto-lineup) and
strength of schedule (who they play, especially in the fantasy playoff weeks
15-17). We pull each team's schedule from ESPN, derive the bye as the missing
week, and approximate schedule difficulty from opponents' projected wins.
"""

import datetime

import nfl_awards          # racing cache + _team_wins
import pro_data

_racing = nfl_awards.racing
# ESPN abbreviation is the canonical key; map the common alternates onto it so a
# player whose team code came from a different feed (Sleeper) still resolves.
_CANON = {"WAS": "WSH", "JAC": "JAX", "OAK": "LV", "LA": "LAR", "SD": "LAC",
          "STL": "LAR", "ARZ": "ARI", "CLV": "CLE", "HST": "HOU", "BLT": "BAL"}


def _canon(ab):
    ab = (ab or "").upper()
    return _CANON.get(ab, ab)


def _build(season):
    teams = pro_data.teams("nfl") or []
    out = {}

    def one(t):
        tid = t.get("id")
        ab = t.get("abbrev") or t.get("abbr")
        if not tid or not ab:
            return None
        try:
            d = _racing._get_json(
                f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{tid}/schedule?season={season}&seasontype=2",
                timeout=15)
        except Exception:
            return None
        opps, weeks = {}, set()
        for e in d.get("events", []):
            wk = (e.get("week") or {}).get("number")
            if not wk:
                continue
            weeks.add(wk)
            for c in (e.get("competitions") or [{}])[0].get("competitors", []):
                cab = (c.get("team") or {}).get("abbreviation")
                if cab and _canon(cab) != _canon(ab):
                    opps[wk] = _canon(cab)
        bye = next((w for w in range(1, 19) if w not in weeks), None)
        return _canon(ab), {"bye": bye, "opps": opps}

    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for r in ex.map(one, teams):
            if r:
                out[r[0]] = r[1]

    # Strength of schedule from opponents' projected wins (easier = lower).
    tw = {}
    try:
        tw = {_canon(k): v for k, v in (nfl_awards._team_wins() or {}).items()}
    except Exception:
        pass
    for info in out.values():
        ow = [tw.get(o, 8.5) for o in info["opps"].values()]
        info["sos"] = round(sum(ow) / len(ow), 1) if ow else 8.5
        po = [tw.get(info["opps"].get(w), 8.5) for w in (15, 16, 17) if info["opps"].get(w)]
        info["playoff_sos"] = round(sum(po) / len(po), 1) if po else 8.5
    return out


def schedule(season=None):
    """{ESPN_abbr: {bye, opps{week:opp}, sos, playoff_sos}}, cached a week."""
    season = season or datetime.date.today().year
    return _racing._cached(("nfl_schedule", season), 7 * 86400, lambda: _build(season)) or {}


def info(team_abbr):
    return schedule().get(_canon(team_abbr))


def bye(team_abbr):
    i = info(team_abbr)
    return i.get("bye") if i else None
