"""What the DraftKings public can see before it builds, frozen at each kickoff.

research/reports/cl_field2.md (2026-11-26): the served classic field is half
as concentrated per player as the real one and its copied builds are another
projection's optimum. Testing a field model built on what the public reads
needs those readings AS THEY STOOD before lock, and two of them are kept by
nothing:

  dk     DraftKings' own draft screen for the Sunday main slate: every
         player's salary, his average DraftKings points a game (FPPG), the
         opponent's rank against his position and his status tag. DraftKings
         still serves a past draft group today, but nothing promises it will,
         and a status tag is only ever the latest.
  espn   ESPN's season-long start and roster percentages (how much of the
         casual public is starting a player THIS week, the nearest free
         reading of public ownership there is) and its projection for the
         defenses, which nfl_props_log's ESPN log does not keep.

Sleeper's and ESPN's player projections are already frozen by nfl_props_log;
this does not repeat them. Both blocks live INSIDE that module's weekly store
(key nfl_props_<season>_wNN, under "fieldcap"), so the one export the
error-log workflow already commits under props/ carries them and no route or
workflow changes. Both writers run one after the other in the recorder's one
thread (mlb_recorder), so the load-modify-save cannot interleave.

Frozen as nfl_props_log freezes: a player's row is rewritten on every pass
while his team's game is still ahead of its kickoff by LEAD_S, and never
again after -- DraftKings classic allows late swap, so each game's kickoff IS
that game's lock. Read by nothing in production: the registered protocol
(research/reports/cl_field2_protocol.md) is its only reader.

Cost, measured 2026-11-26: the draftables call is one ~1 MB response per
pass; ESPN's fantasy API serves ~24 KB a player, read in pages of 50 best
projection first until under PROJ_FLOOR, at most every ESPN_EVERY_S (the
same budget nfl_props_log's own ESPN read was measured at). The stored block
is a few hundred small rows.
"""
import time

import errlog

EVERY_S = 1800              # one pass every half hour, as the prop log
ESPN_EVERY_S = 3 * 3600
ESPN_PAGE, ESPN_PAGES, PROJ_FLOOR = 50, 10, 0.5
# ESPN lineup slots: QB 0, RB 2, WR 4, TE 6, D/ST 16 (no kicker: DraftKings'
# classic roster has none)
ESPN_SLOTS = (0, 2, 4, 6, 16)
ESPN_POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 16: "DST"}
_FPPG, _OPRK = 90, -2       # DraftKings' draftStatAttributes ids (dk._AVG_PPG_ATTR_NFL; -2 read off the live pool)
_last = [0.0]
_espn_last = [0.0]


def dk_rows(draftables):
    """[{"name", "team", "pos", "salary", "fppg", "oprk", "status"}] from a
    draftables payload, one row a player (DraftKings lists one per roster
    slot). A missing or unreadable figure is None, never 0."""
    def num(a, key, cast):
        try:
            return cast((a or {}).get(key))
        except (TypeError, ValueError):
            return None
    out, seen = [], set()
    for x in (draftables or {}).get("draftables") or []:
        nm = " ".join(str(x.get("displayName") or "").split())
        if not nm or nm in seen:
            continue
        seen.add(nm)
        at = {a.get("id"): a for a in (x.get("draftStatAttributes") or [])}
        out.append({"name": nm, "team": x.get("teamAbbreviation") or "", "pos": x.get("position"),
                    "salary": x.get("salary"), "fppg": num(at.get(_FPPG), "value", float),
                    "oprk": num(at.get(_OPRK), "sortValue", int), "status": (x.get("status") or "").upper() or None})
    return out


def espn_rows(season, week, get=None):
    """[{"name", "team_id", "pos", "proj", "started", "owned", "change"}]:
    every QB, RB, WR, TE and D/ST ESPN projects at PROJ_FLOOR+ points for
    `week`, best first, with the share of ESPN leagues starting and rostering
    him. `get(url, headers)` is for tests."""
    import json
    import nfl_props_log
    import racing
    get = get or (lambda url, hdr: racing._get_json(url, timeout=20, headers=hdr))
    sid = f"11{int(season)}{int(week)}"      # source 1 (projected), split 1 (one week)
    url = nfl_props_log._ESPN_PROJ.format(season=int(season), week=int(week))
    out = []
    for page in range(ESPN_PAGES):
        flt = {"players": {"filterSlotIds": {"value": list(ESPN_SLOTS)},
                           "filterStatsForTopScoringPeriodIds": {"value": 1, "additionalValue": [sid]},
                           "limit": ESPN_PAGE, "offset": page * ESPN_PAGE,
                           "sortAppliedStatTotal": {"sortAsc": False, "sortPriority": 1, "value": sid}}}
        players = (get(url, {"X-Fantasy-Filter": json.dumps(flt)}) or {}).get("players") or []
        low = False
        for p in players:
            pl = p.get("player") or {}
            st = next((x for x in pl.get("stats") or [] if str(x.get("id")) == sid), None)
            pos = ESPN_POS.get(pl.get("defaultPositionId"))
            pts = (st or {}).get("appliedTotal")
            if pts is not None and pts < PROJ_FLOOR:
                low = True
            if not pos or not pl.get("fullName") or pts is None or pts < PROJ_FLOOR:
                continue
            own = pl.get("ownership") or {}

            def pct(k):
                v = own.get(k)
                return None if v is None else round(float(v), 2)
            out.append({"name": pl["fullName"], "team_id": str(pl.get("proTeamId")), "pos": pos,
                        "proj": round(float(pts), 3), "started": pct("percentStarted"),
                        "owned": pct("percentOwned"), "change": pct("percentChange")})
        if low or len(players) < ESPN_PAGE:
            break
    return out


def snapshot(now=None, payload=None, slate="fetch", draftables=None, espn=None):
    """One pass. Returns {"key", "dk", "espn", "draft_group_id"} (rows written
    this pass), or None when ESPN's scoreboard names no week. `payload` (the
    scoreboard), `slate` (the main slate row, None for none), `draftables`
    and `espn` (espn_rows' rows) are for tests."""
    import deep_cache
    import kalshi_nfl
    import nfl_props_log
    import racing
    now = time.time() if now is None else now
    if payload is None:
        payload = racing._get_json(nfl_props_log._ESPN) or {}
    season, week, games = nfl_props_log.espn_games(payload)
    if not season or not week:
        return None
    live, kicks = set(), []            # team codes still ahead of their kickoff; every kickoff of the week
    for codes, g in games.items():
        if g.get("kickoff"):
            kicks.append(g["kickoff"])
            if g.get("state") == "pre" and g["kickoff"] - now >= nfl_props_log.LEAD_S:
                live |= set(codes)
    key = nfl_props_log.KEY.format(season=int(season), week=int(week))
    if not live:
        return {"key": key, "dk": 0, "espn": 0, "draft_group_id": None}
    # the main slate must be THIS scoreboard week's: DraftKings posts next
    # Sunday's pool days early, and its rows would freeze under the wrong week
    dg, n_dk, rows = None, 0, []
    try:
        if slate == "fetch":
            import dk
            slate = dk.main_slate(dk.slates("nfl"), now=now)
        if slate:
            import nfl_dfs
            st = nfl_dfs.iso_ts(slate.get("starts"))
            if st is not None and kicks and min(kicks) - 3600 <= st <= max(kicks):
                dg = slate.get("draft_group_id")
                if draftables is None:
                    import dk
                    draftables = dk._get(dk._DRAFTABLES.format(dg=dg), timeout=30)
                rows = dk_rows(draftables)
    except Exception as e:
        errlog.note("FIELDCAP-dk", e)
    erows = espn
    if erows is None and now - _espn_last[0] >= ESPN_EVERY_S:
        _espn_last[0] = now
        try:
            erows = espn_rows(int(season), int(week))
        except Exception as e:
            errlog.note("FIELDCAP-espn", e)
    if not rows and not erows:
        return {"key": key, "dk": 0, "espn": 0, "draft_group_id": dg}
    # loaded only now, after every network read: the prop log's own pass ran
    # just before this one in the same thread, and nothing else writes the key
    store, _ts = deep_cache.load(key)
    store = store or {"season": int(season), "week": int(week), "games": {}}
    cap = store.setdefault("fieldcap", {"dk": {}, "espn": {}})
    if rows:
        if cap.get("draft_group_id") not in (None, dg):
            # a second main slate inside one week would mix two price lists
            errlog.note("FIELDCAP-group", f"week {week}: draft group {dg} after {cap.get('draft_group_id')}")
        else:
            cap["draft_group_id"] = dg
            for r in rows:
                if kalshi_nfl._canon(r["team"]) in live:
                    cap["dk"][r["name"]] = dict(r, ts=round(now))
                    n_dk += 1
    n_espn = 0
    if erows:
        tid = {}
        for e in payload.get("events") or []:
            for x in ((e.get("competitions") or [{}])[0]).get("competitors") or []:
                t = x.get("team") or {}
                if t.get("id") is not None and t.get("abbreviation"):
                    tid[str(t["id"])] = kalshi_nfl._canon(t["abbreviation"])
        for r in erows:
            team = tid.get(str(r.get("team_id")))
            if team in live and r.get("name"):
                cap["espn"][kalshi_nfl._norm(r["name"])] = {
                    "name": r["name"], "team": team, "pos": r.get("pos"), "ts": round(now),
                    "proj": r.get("proj"), "started": r.get("started"), "owned": r.get("owned"),
                    "change": r.get("change")}
                n_espn += 1
    if n_dk or n_espn:
        deep_cache.save(key, store)
    return {"key": key, "dk": n_dk, "espn": n_espn, "draft_group_id": dg}


def tick():
    """The recorder loop's hook (mlb_recorder, right after nfl_props_log's):
    one pass every EVERY_S."""
    now = time.time()
    if now - _last[0] < EVERY_S:
        return None
    _last[0] = now
    try:
        return snapshot(now)
    except Exception as e:
        errlog.note("FIELDCAP-snapshot", e)
        return None
