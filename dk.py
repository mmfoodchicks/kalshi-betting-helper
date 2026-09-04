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

import errlog
import racing

_LOBBY = "https://www.draftkings.com/lobby/getcontests?sport={sport}"
_DRAFTABLES = "https://api.draftkings.com/draftgroups/v1/draftgroups/{dg}/draftables?format=json"

# Our sport keys -> DK's lobby sport codes.
SPORTS = {
    "ufc": "MMA", "mlb": "MLB", "nfl": "NFL", "nba": "NBA", "nhl": "NHL",
    "golf": "GOLF", "nascar": "NAS", "f1": "MOTOR", "soccer": "SOC", "lol": "LOL",
}
_AVG_PPG_ATTR = 408          # DK's "AvgPointsPerGame" stat id (MLB and most)
_AVG_PPG_ATTR_NFL = 90       # ...NFL files it under 90 (read off the live
                             # week-1 draftables: [(90, '8.6'), (-2, '22nd')])
# DK lobby ContestTypeIds, read off the live NFL lobby (Sep 2026): Classic is
# the full-slate game the optimizer is built for; Showdown (96) is a one-game
# captain format; Sit & Go (145) is a rebranded Classic; 158/159 are "Madden
# Stream" contests -- a VIDEO-GAME simulation, with fake "SF @ NE" games and
# real player names, that the picker chose over week 1 because it started
# "tonight". Never a real slate for any sport.
_CLASSIC_TYPES = {"nfl": {21}}
_NEVER_TYPES = {158, 159}
_OPP_PITCHER_ATTR = 112      # MLB: opposing starter + handedness
_OPENER_ATTR = 135           # MLB: "PO" badge -- listed pitcher is an OPENER
_BULK_ATTR = 136             # MLB: "PLR" badge -- the probable long reliever
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
            tag = (g.get("ContestStartTimeSuffix") or "").strip() or None
            ctype = g.get("ContestTypeId")
            if ctype in _NEVER_TYPES or "madden" in (tag or "").lower():
                continue
            out.append({"draft_group_id": dg, "sport": sport,
                        "starts": g.get("StartDateEst"),
                        "games": g.get("GameCount"),
                        "contest_type": ctype,
                        "tag": tag})
        out.sort(key=lambda s: (str(s["starts"] or ""), -(s["games"] or 0)))
        if not out:
            # A throttled or mangled lobby response parses to an EMPTY list,
            # and an empty list is a cacheable "success" -- which pinned "DK
            # has nothing posted" for 15 minutes over a one-request blink.
            # In August, mid-slate, that is never the truth; treat empty as a
            # failed fetch so the next call retries.
            errlog.note("DK-lobby-empty",
                        msg=f"{sport}: lobby answered with no draft groups")
            return None
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
            # DK's pitcher-role badges, verified against the app's own labels:
            # game attribute 135 = "PO" (OPENER -- a reliever scripted for 1-2
            # innings before handing off) and 136 = "PLR" (the probable LONG
            # reliever / bulk arm who inherits the real innings behind him).
            # An opener at the P slot is a trap the lineup card must call out.
            role = ("opener" if str(gattrs.get(_OPENER_ATTR)).lower() == "true"
                    else "bulk" if str(gattrs.get(_BULK_ATTR)).lower() == "true"
                    else None)
            seen[nm] = {
                "name": nm, "salary": x.get("salary"),
                "position": x.get("position"), "roster_pos": x.get("position"),
                "game": comp.get("name") or "",
                "starts": comp.get("startTime"),
                "team": x.get("teamAbbreviation") or "",
                "avg_ppg": attrs.get(_AVG_PPG_ATTR, attrs.get(_AVG_PPG_ATTR_NFL)),
                "status": status or None,
                "opp_pitcher": gattrs.get(_OPP_PITCHER_ATTR),
                "role": role,
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
    candidates = [draft_group_id] if draft_group_id else []
    if not candidates:
        # DK's StartDateEst strings are EASTERN time. Comparing them against
        # the server's local clock (UTC on the host) read tonight's entire
        # slate as already started and picked next week's -- whose player pool
        # DK had not posted yet, so the auto-load returned nothing at all.
        import clock
        now = clock.now_et().strftime("%Y-%m-%dT%H:%M:%S")
        today = now[:10]
        upcoming = [s for s in sl if str(s.get("starts") or "") >= now] or sl
        # Tonight first: the biggest slate STARTING TODAY beats a bigger one
        # next Tuesday. Then walk the list -- a group with no players posted
        # yet (DK lists future groups early) must not end the search.
        # The MAIN slate first where the sport has one: for NFL that is the
        # Classic full-slate group, and a Thursday-night Showdown (one game,
        # captain slots) or a Madden Stream that happens to start "tonight"
        # must not outrank Sunday's thirteen games. Sports without a known
        # Classic id keep the tonight-first rule as it was.
        main = _CLASSIC_TYPES.get((sport or "").lower())
        if main:
            classic = [s for s in upcoming if s.get("contest_type") in main]
            if classic:
                upcoming = classic
        tonight = [s for s in upcoming if str(s.get("starts") or "")[:10] == today]
        ordered = (sorted(tonight, key=lambda s: -(s.get("games") or 0))
                   + sorted((s for s in upcoming if s not in tonight),
                            key=lambda s: (str(s.get("starts") or ""),
                                           -(s.get("games") or 0))))
        candidates = [s["draft_group_id"] for s in ordered[:6]]
    pool, dg = None, None
    for cand in candidates:
        pool = players(cand)
        if pool:
            dg = cand
            break
    if not pool:
        return None
    csv_text = salaries_csv(dg, exclude_games=exclude_games)
    if not csv_text:
        return None
    dropped = [p["name"] for p in pool if not p["available"]]
    return {"csv": csv_text, "draft_group_id": dg, "sport": sport,
            "n_players": sum(1 for p in pool if p["available"]),
            "dropped": dropped[:40], "n_dropped": len(dropped),
            # Pitcher roles the CSV format can't carry (opener / bulk badges);
            # the DFS builder tags its pool from this so the card can warn.
            "roles": {p["name"]: p["role"] for p in pool if p.get("role")},
            "contests": contests(sport, dg)[:10],
            "slates": sl[:10]}
