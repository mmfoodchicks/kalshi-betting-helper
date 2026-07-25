"""DraftKings slate loader — salaries and contest metadata straight from DK's
public endpoints, so the DFS builder never needs a hand-pasted CSV.

Everything here is the same public, unauthenticated data the DK lobby serves to
a logged-out browser: which slates exist, each slate's player pool with salaries
and roster positions, and the posted contests (entry fee, field size, prize
pool). No login, no scraping of a member area.

Two endpoints do the work:
  lobby/getcontests?sport=X                  -> draft groups + contests for a sport
  draftgroups/v1/draftgroups/{id}/draftables -> the player pool with salaries

What we take from DK, and what we deliberately don't:
  * SALARIES / roster slots / the bout-or-game key — only DK has these, and the
    optimizer can't run without them. This is the reason the module exists.
  * Contest metadata — entry fee, field size, prize pool and top prize, so the
    contest simulator's parameters match the contest you're actually entering
    instead of being typed in by hand.
  * Player status (IL / OUT / DTD) — used ONLY to flag and optionally drop
    unavailable players, never to rate them.
  * NOT their AvgPointsPerGame as a projection. It's a lagging season average;
    our own simulators project every player and are strictly better. It rides
    along in the CSV only because the DK format has the column.
"""

import csv
import io
import time

import racing

_LOBBY = "https://www.draftkings.com/lobby/getcontests?sport={sport}"
_DRAFTABLES = "https://api.draftkings.com/draftgroups/v1/draftgroups/{dg}/draftables?format=json"

# Our sport keys -> DK's lobby sport codes.
SPORTS = {
    "ufc": "MMA", "mlb": "MLB", "nfl": "NFL", "nba": "NBA", "nhl": "NHL",
    "golf": "GOLF", "nascar": "NAS", "f1": "MOTOR", "soccer": "SOC", "lol": "LOL",
}
_AVG_PPG_ATTR = 408          # DK's "AvgPointsPerGame" stat id
_OPP_PITCHER_ATTR = 112      # MLB: opposing starter + handedness
_UNAVAILABLE = {"IL", "OUT", "NA", "SUSP"}


def _get(url, timeout=25):
    return racing._get_json(url, timeout=timeout)


def slates(sport):
    """[{draft_group_id, starts, games, sport}] for a sport, soonest first.
    Empty when DK has nothing posted (offseason / no slate today)."""
    code = SPORTS.get((sport or "").lower())
    if not code:
        return []

    def build():
        try:
            d = _get(_LOBBY.format(sport=code))
        except Exception:
            return None
        out = []
        for g in d.get("DraftGroups") or []:
            dg = g.get("DraftGroupId")
            if not dg:
                continue
            out.append({"draft_group_id": dg, "sport": sport,
                        "starts": g.get("StartDateEst"),
                        "games": g.get("GameCount"),
                        "tag": (g.get("ContestStartTimeSuffix") or "").strip() or None})
        out.sort(key=lambda s: (str(s["starts"] or ""), -(s["games"] or 0)))
        return out
    return racing._cached(("dk_slates", sport), 900, build) or []


def contests(sport, draft_group_id=None):
    """Posted contests, richest first: [{name, entry_fee, entries, max_entries,
    prize_pool, first_prize, draft_group_id}]. Lets the contest simulator use the
    real field size and payout instead of hand-typed numbers."""
    code = SPORTS.get((sport or "").lower())
    if not code:
        return []

    def build():
        try:
            d = _get(_LOBBY.format(sport=code))
        except Exception:
            return None
        out = []
        for c in d.get("Contests") or []:
            try:
                out.append({
                    "name": c.get("n"), "draft_group_id": c.get("dg"),
                    "entry_fee": float(c.get("a") or 0),
                    "entries": int(c.get("m") or 0),          # max field size
                    "entered": int(c.get("nt") or 0),
                    "prize_pool": float(c.get("po") or 0),
                    "max_entries_per_user": c.get("mec"),
                    "id": c.get("id")})
            except (TypeError, ValueError):
                continue
        out.sort(key=lambda x: -(x["prize_pool"] or 0))
        return out
    rows = racing._cached(("dk_contests", sport), 600, build) or []
    if draft_group_id:
        rows = [r for r in rows if str(r.get("draft_group_id")) == str(draft_group_id)]
    return rows


def players(draft_group_id):
    """The slate's player pool: [{name, salary, position, roster_pos, game, team,
    avg_ppg, status, opp_pitcher, available}] — de-duplicated (DK lists a player
    once per roster slot)."""
    def build():
        try:
            d = _get(_DRAFTABLES.format(dg=draft_group_id), timeout=30)
        except Exception:
            return None
        seen = {}
        for x in d.get("draftables") or []:
            nm = x.get("displayName")
            if not nm or nm in seen:
                continue
            attrs = {a.get("id"): a.get("value") for a in (x.get("draftStatAttributes") or [])}
            gattrs = {a.get("id"): a.get("value") for a in (x.get("playerGameAttributes") or [])}
            pattrs = {a.get("name"): a.get("value") for a in (x.get("playerAttributes") or [])}
            comp = x.get("competition") or {}
            status = (x.get("status") or "").upper()
            seen[nm] = {
                "name": nm, "salary": x.get("salary"),
                "position": x.get("position"), "roster_pos": x.get("position"),
                "game": comp.get("name") or "",
                "starts": comp.get("startTime"),
                "team": x.get("teamAbbreviation") or "",
                "avg_ppg": attrs.get(_AVG_PPG_ATTR),
                "status": status or None,
                "opp_pitcher": gattrs.get(_OPP_PITCHER_ATTR),
                "hand": pattrs.get("Handedness"),
                "bats": pattrs.get("Bat-Handedness"),
                "dk_id": x.get("playerDkId"),
                # DK marks the injured/inactive; a player on the IL can still be
                # listed, so this is what lets us drop him from the pool.
                "available": not (x.get("isDisabled") or status in _UNAVAILABLE)}
        return list(seen.values()) or None
    return racing._cached(("dk_players", draft_group_id), 600, build) or []


def salaries_csv(draft_group_id, drop_unavailable=True, exclude_games=None):
    """The slate as a DraftKings-format CSV string — the exact shape
    simulate.parse_dk_csv already reads, so it drops straight into the DFS
    builder with no pasting.

    drop_unavailable: leave out players DK flags IL/OUT/inactive.
    exclude_games:    iterable of game/bout substrings to drop entirely (a
                      postponed fight or a rained-out game DK hasn't pulled yet).
    """
    pool = players(draft_group_id)
    if not pool:
        return None
    skip = [s.lower() for s in (exclude_games or []) if s]
    rows = [["Position", "Name + ID", "Name", "ID", "Roster Position", "Salary",
             "Game Info", "TeamAbbrev", "AvgPointsPerGame"]]
    for p in pool:
        if drop_unavailable and not p["available"]:
            continue
        if skip and any(s in (p["game"] or "").lower() for s in skip):
            continue
        if not p.get("salary"):
            continue
        rows.append([p["position"] or "", f"{p['name']} ({p['dk_id']})", p["name"],
                     p["dk_id"], p["roster_pos"] or "", p["salary"], p["game"],
                     p["team"], p["avg_ppg"] if p["avg_ppg"] not in (None, "-") else ""])
    if len(rows) < 2:
        return None
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue()


def slate_for(sport, draft_group_id=None, exclude_games=None):
    """One call for the DFS builder: pick a slate (the soonest with the most
    games unless one is named) and return
    {csv, draft_group_id, players, n_players, dropped, contests}."""
    sl = slates(sport)
    if not sl:
        return None
    dg = draft_group_id
    if dg is None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        upcoming = [s for s in sl if str(s.get("starts") or "") >= now] or sl
        dg = max(upcoming, key=lambda s: (s.get("games") or 0))["draft_group_id"]
    pool = players(dg)
    if not pool:
        return None
    csv_text = salaries_csv(dg, exclude_games=exclude_games)
    if not csv_text:
        return None
    dropped = [p["name"] for p in pool if not p["available"]]
    return {"csv": csv_text, "draft_group_id": dg, "sport": sport,
            "n_players": sum(1 for p in pool if p["available"]),
            "dropped": dropped[:40], "n_dropped": len(dropped),
            "contests": contests(sport, dg)[:10],
            "slates": sl[:10]}
