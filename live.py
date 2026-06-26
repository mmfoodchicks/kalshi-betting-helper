"""Confirmed live games across every league we track.

The Sports → Live tab used to only ever show baseball. It tried to *infer*
in-play status for other sports from Kalshi market timing, but those markets
expire at series/day end (often 1-2 weeks out), so "closing within 4h" never
fired -- nothing but MLB ever surfaced.

The reliable signal is the league's own scoreboard, exactly like the MLB feed:
ESPN publishes a per-league scoreboard whose every event carries a status state
of pre / in / post. We pull the leagues we track and keep the ones that are
actually `in` progress, with their live score and game clock. MLB keeps its
richer dedicated feed (inning + base/out state) and is merged in by the caller.
"""

import concurrent.futures as _cf

import racing

# ESPN site-API scoreboard paths for the team sports we track. These all share
# the same clean JSON shape, so one parser handles them. (Tennis/MMA use a
# different, much larger payload that the proxy truncates, so they're left to
# their own dedicated model tabs rather than risk a flaky live read.)
_LEAGUES = [
    ("🏀 WNBA", "basketball/wnba"),
    ("🏀 NBA", "basketball/nba"),
    ("🏒 NHL", "hockey/nhl"),
    ("🏈 NFL", "football/nfl"),
    ("🏈 College FB", "football/college-football"),
    ("⚽ MLS", "soccer/usa.1"),
    ("⚽ EPL", "soccer/eng.1"),
]


def _scoreboard(path):
    url = f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
    try:
        return racing._get_json(url, timeout=12)
    except Exception:
        return None


def _parse(label, d):
    out = []
    for e in (d or {}).get("events", []):
        comp = (e.get("competitions") or [{}])[0]
        state = (((comp.get("status") or {}).get("type") or {}).get("state"))
        if state != "in":                       # only games in progress
            continue
        cs = comp.get("competitors") or []
        home = next((c for c in cs if c.get("homeAway") == "home"), None)
        away = next((c for c in cs if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue

        def nm(c):
            t = c.get("team") or {}
            return t.get("shortDisplayName") or t.get("displayName") or t.get("name") or "?"
        detail = (((e.get("status") or comp.get("status") or {}).get("type") or {})
                  .get("shortDetail")) or "Live"
        out.append({
            "sport": label, "confirmed": True,
            "title": f"{nm(away)} @ {nm(home)}",
            "score": f"{away.get('score', 0)}–{home.get('score', 0)}",
            "detail": detail,
        })
    return out


def confirmed_live():
    """Every tracked team-sport game currently in progress (ESPN scoreboards).
    Best-effort and defensive: a league that errors or truncates is just skipped,
    never fails the whole tab. MLB is added separately by the caller."""
    games = []

    def one(item):
        label, path = item
        return _parse(label, _scoreboard(path))

    with _cf.ThreadPoolExecutor(max_workers=len(_LEAGUES)) as ex:
        for r in ex.map(one, _LEAGUES):
            games.extend(r)
    return games
