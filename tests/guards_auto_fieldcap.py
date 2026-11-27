"""Guards for dfs_field_capture (2026-11-26): what the DraftKings public can
see before it builds, frozen at each kickoff inside the prop log's store.
Runs in the suite's AUTO section; alone: python3 tests/guards_auto_fieldcap.py

Each check names the failure it would have caught: a figure read as 0 when
DraftKings showed none, a row rewritten after its kickoff (in-game
information in a pre-game record), next Sunday's price list frozen under
this week, the prop log's own keys lost to the second writer, and the
protocol's file drifting from the hash registered beside it.
"""
import contextlib
import hashlib
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("VIGIL_NO_BG", "1")


@contextlib.contextmanager
def _sandbox():
    """The deep store in a temp dir, the module's throttles reset; restored."""
    import deep_cache
    import dfs_field_capture as F
    tmp = tempfile.mkdtemp()
    saved = (deep_cache.CACHE_DIR, F._last[0], F._espn_last[0])
    try:
        deep_cache.CACHE_DIR = os.path.join(tmp, "deep")
        os.makedirs(deep_cache.CACHE_DIR, exist_ok=True)
        F._last[0] = F._espn_last[0] = 0.0
        yield tmp
    finally:
        deep_cache.CACHE_DIR, F._last[0], F._espn_last[0] = saved


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(ts))


def _payload(t0, g1_state="pre"):
    def ev(i, ko, state, home, hid, away, aid):
        return {"id": i, "date": _iso(ko), "competitions": [{"status": {"type": {"state": state}},
                "venue": {"fullName": "X"}, "competitors": [
                    {"homeAway": "home", "team": {"abbreviation": home, "id": hid}},
                    {"homeAway": "away", "team": {"abbreviation": away, "id": aid}}]}]}
    return {"season": {"year": 2026}, "week": {"number": 13},
            "events": [ev("g1", t0 + 7200, g1_state, "DET", "8", "LAR", "14"),
                       ev("g2", t0 - 600, "in", "KC", "12", "BUF", "2")]}


def _draftable(name, team, pos, salary, fppg="12.5", oprk="21", status="None"):
    at = []
    if fppg is not None:
        at.append({"id": 90, "value": fppg, "sortValue": fppg})
    if oprk is not None:
        at.append({"id": -2, "value": oprk + "st", "sortValue": oprk})
    return {"displayName": name, "teamAbbreviation": team, "position": pos, "salary": salary,
            "status": status, "draftStatAttributes": at}


def _eplayer(name, tid, pos, pts, started=None, owned=None, sid="11202613"):
    pl = {"fullName": name, "proTeamId": tid, "defaultPositionId": pos,
          "stats": [{"id": "11202612", "appliedTotal": 99.0}, {"id": sid, "appliedTotal": pts}]}
    if started is not None:
        pl["ownership"] = {"percentStarted": started, "percentOwned": owned, "percentChange": 0.126}
    return {"player": pl}


def run(ck):
    import deep_cache
    import dfs_field_capture as F
    import nfl_props_log as N

    # ---- DraftKings' draft screen ---------------------------------------------------------------
    rows = F.dk_rows({"draftables": [
        _draftable("Emmett  Johnson", "KC", "RB", 4700, "11.3", "30"),
        _draftable("Emmett Johnson", "KC", "RB", 4700, "99.9", "1"),          # the FLEX row of the same man
        _draftable("New Guy", "DET", "WR", 3000, None, None, ""),
        _draftable("Odd Guy", "DET", "WR", 3100, "-", "x"), {"displayName": ""}]})
    by = {r["name"]: r for r in rows}
    ck("FC1 DraftKings' pool is one row a player (his first), FPPG and the opponent rank read off attributes 90 and -2",
       len(rows) == 3 and by["Emmett Johnson"]["fppg"] == 11.3 and by["Emmett Johnson"]["oprk"] == 30
       and by["Emmett Johnson"]["salary"] == 4700 and by["Emmett Johnson"]["status"] == "NONE", str(rows))
    ck("FC2 a figure DraftKings does not show (or shows unreadable) is None, never 0: a rookie's missing average must "
       "not read as a zero-point player",
       by["New Guy"]["fppg"] is None and by["New Guy"]["oprk"] is None and by["New Guy"]["status"] is None
       and by["Odd Guy"]["fppg"] is None and by["Odd Guy"]["oprk"] is None, str(by))

    # ---- ESPN's start percentages ------------------------------------------------------------------
    calls = []
    pages = [[_eplayer("Jahmyr Gibbs", 8, 2, 27.97, 98.97, 99.94), _eplayer("Lions D/ST", 8, 16, 7.5, 34.71, 56.97)],
             [_eplayer("Kick Er", 8, 5, 9.0, 80.0, 90.0), _eplayer("No Own", 14, 3, 6.0),
              _eplayer("Low Guy", 14, 3, 0.3, 1.0, 2.0)],
             [_eplayer("Never Read", 14, 3, 5.0, 1.0, 2.0)]]

    def _get(url, hdr):
        f = json.loads(hdr["X-Fantasy-Filter"])["players"]
        calls.append((url, f))
        return {"players": pages[f["offset"] // f["limit"]]}
    old = F.ESPN_PAGE
    F.ESPN_PAGE = 2
    try:
        er = F.espn_rows(2026, 13, get=_get)
    finally:
        F.ESPN_PAGE = old
    f0 = calls[0][1]
    ck("FC3 ESPN is read for the week asked, best projection first, the defenses included (slot 16) and no kicker; it "
       "stops at the first page reaching the floor; only that week's projection entry is read",
       [r["name"] for r in er] == ["Jahmyr Gibbs", "Lions D/ST", "No Own"] and len(calls) == 2
       and "scoringPeriodId=13&" in calls[0][0] and f0["filterSlotIds"]["value"] == [0, 2, 4, 6, 16]
       and f0["sortAppliedStatTotal"]["value"] == "11202613" and er[0]["proj"] == 27.97 and er[1]["pos"] == "DST",
       str((er, len(calls))))
    ck("FC4 the start and roster percentages are ESPN's own; a player with no ownership block reads None, not 0",
       er[0]["started"] == 98.97 and er[0]["owned"] == 99.94 and er[0]["change"] == 0.13
       and er[2]["started"] is None and er[2]["owned"] is None, str(er))

    # ---- the pass: written while a game is ahead of its kickoff, frozen after --------------------------
    t0 = 1_800_000_000.0
    slate = {"draft_group_id": 157066, "starts": _iso(t0 + 7200).replace("Z", ":00.0000000Z")}
    dr = {"draftables": [_draftable("Jahmyr Gibbs", "DET", "RB", 8800, "24.0", "12"),
                         _draftable("Josh Allen", "BUF", "QB", 7700, "23.0", "5")]}
    es = [{"name": "Jahmyr Gibbs", "team_id": "8", "pos": "RB", "proj": 27.97, "started": 98.97, "owned": 99.94, "change": 0.0},
          {"name": "Josh Allen", "team_id": "2", "pos": "QB", "proj": 22.4, "started": 89.5, "owned": 99.9, "change": 0.0},
          {"name": "Bye Guy", "team_id": "33", "pos": "WR", "proj": 9.0, "started": 1.0, "owned": 2.0, "change": 0.0}]
    key = N.KEY.format(season=2026, week=13)
    with _sandbox():
        deep_cache.save(key, {"season": 2026, "week": 13, "games": {"26NOV29LARDET": {"ts": 1}},
                              "sleeper": {"jahmyr gibbs": {"name": "Jahmyr Gibbs"}}})
        r1 = F.snapshot(t0, payload=_payload(t0), slate=slate, draftables=dr, espn=es)
        s1, _ts = deep_cache.load(key)
        cap = s1.get("fieldcap") or {}
        ck("FC5 a pass writes the players of teams still ahead of their kickoff, under the draft group it read",
           r1 == {"key": key, "dk": 1, "espn": 1, "draft_group_id": 157066} and cap.get("draft_group_id") == 157066
           and cap["dk"]["Jahmyr Gibbs"]["fppg"] == 24.0 and cap["dk"]["Jahmyr Gibbs"]["ts"] == round(t0)
           and cap["espn"]["jahmyr gibbs"]["started"] == 98.97 and cap["espn"]["jahmyr gibbs"]["team"] == "DET",
           str((r1, cap)))
        ck("FC6 a team whose game has started is never written: an in-game status tag or start share must not stand in "
           "for a pre-game one (CLAUDE.md, the fake +14.8c CLV); a bye team has nothing to keep",
           "Josh Allen" not in cap["dk"] and "josh allen" not in cap["espn"] and "bye guy" not in cap["espn"], str(cap))
        ck("FC7 the capture lives INSIDE the prop log's weekly store and leaves its keys as they were",
           s1["sleeper"] == {"jahmyr gibbs": {"name": "Jahmyr Gibbs"}} and s1["games"] == {"26NOV29LARDET": {"ts": 1}}
           and s1["season"] == 2026 and s1["week"] == 13, str(sorted(s1)))
        # five minutes before the kickoff the row is frozen, whatever DraftKings shows afterwards
        dr2 = {"draftables": [_draftable("Jahmyr Gibbs", "DET", "RB", 8800, "31.5", "12", "OUT")]}
        es2 = [dict(es[0], started=5.0)]
        r2 = F.snapshot(t0 + 7200 - 200, payload=_payload(t0), slate=slate, draftables=dr2, espn=es2)
        r3 = F.snapshot(t0 + 9000, payload=_payload(t0, "in"), slate=slate, draftables=dr2, espn=es2)
        s2, _ts = deep_cache.load(key)
        ck("FC8 FROZEN: inside LEAD_S of the kickoff, and after it, nothing is rewritten",
           r2["dk"] == 0 and r2["espn"] == 0 and r3["dk"] == 0 and s2["fieldcap"]["dk"]["Jahmyr Gibbs"]["fppg"] == 24.0
           and s2["fieldcap"]["dk"]["Jahmyr Gibbs"]["status"] == "NONE"
           and s2["fieldcap"]["espn"]["jahmyr gibbs"]["started"] == 98.97, str((r2, r3, s2["fieldcap"])))
        # ...and up to that point the LAST pass wins (the freeze is the latest pre-game reading)
        r4 = F.snapshot(t0 + 3600, payload=_payload(t0), slate=slate, draftables=dr2, espn=es2)
        s3, _ts = deep_cache.load(key)
        ck("FC9 before the freeze the latest reading replaces the earlier one",
           r4["dk"] == 1 and s3["fieldcap"]["dk"]["Jahmyr Gibbs"]["fppg"] == 31.5
           and s3["fieldcap"]["dk"]["Jahmyr Gibbs"]["ts"] == round(t0 + 3600)
           and s3["fieldcap"]["espn"]["jahmyr gibbs"]["started"] == 5.0, str(s3["fieldcap"]))
        other = dict(slate, draft_group_id=999)
        r5 = F.snapshot(t0 + 3700, payload=_payload(t0), slate=other, draftables=dr, espn=[])
        s4, _ts = deep_cache.load(key)
        ck("FC10 a second draft group inside one week is refused, not mixed into the first's price list",
           r5["dk"] == 0 and s4["fieldcap"]["draft_group_id"] == 157066
           and s4["fieldcap"]["dk"]["Jahmyr Gibbs"]["fppg"] == 31.5, str((r5, s4["fieldcap"]["dk"])))
        exp = None
        deep_cache.save(N.LATEST, key)
        exp = N.export(0)
        ck("FC11 the prop log's own export (the route the error-log workflow commits under props/) carries the capture, "
           "and it is plain JSON",
           exp and exp["key"] == key and "fieldcap" in exp["payload"]
           and json.loads(json.dumps(exp["payload"]))["fieldcap"]["dk"]["Jahmyr Gibbs"]["salary"] == 8800, str(exp)[:300])
    with _sandbox():
        nxt = dict(slate, starts=_iso(t0 + 7 * 86400).replace("Z", ":00.0000000Z"))
        r6 = F.snapshot(t0, payload=_payload(t0), slate=nxt, draftables=dr, espn=[])
        s5, _ts = deep_cache.load(key)
        ck("FC12 NEXT Sunday's main slate (DraftKings posts it days early) is not frozen under this week",
           r6 == {"key": key, "dk": 0, "espn": 0, "draft_group_id": None} and s5 is None, str((r6, s5)))
        r7 = F.snapshot(t0, payload=_payload(t0), slate=None, draftables=None, espn=es)
        s6, _ts = deep_cache.load(key)
        ck("FC13 with no main slate the ESPN block is still kept (and a store is started for the week)",
           r7["dk"] == 0 and r7["espn"] == 1 and s6["fieldcap"]["dk"] == {} and "jahmyr gibbs" in s6["fieldcap"]["espn"]
           and s6["games"] == {}, str((r7, s6)))
        ck("FC14 no week on ESPN's scoreboard: nothing is written and nothing raises",
           F.snapshot(t0, payload={}, slate=None, espn=es) is None)

    # ---- the hook -------------------------------------------------------------------------------------
    with _sandbox():
        seen = []
        old_snap = F.snapshot
        F.snapshot = lambda now: seen.append(now) or {"ok": 1}
        try:
            a, b = F.tick(), F.tick()
            F._last[0] = 0.0
            F.snapshot = lambda now: (_ for _ in ()).throw(RuntimeError("boom"))
            c = F.tick()
        finally:
            F.snapshot = old_snap
        ck("FC15 the recorder hook runs one pass every EVERY_S and never raises into the recorder loop",
           a == {"ok": 1} and b is None and len(seen) == 1 and c is None and F.EVERY_S == 1800)
    rec = open(os.path.join(ROOT, "mlb_recorder.py")).read()
    ck("FC16 the recorder calls the capture AFTER the prop log's pass, in the same loop: both write one store, and the "
       "second writer must load what the first just saved",
       "dfs_field_capture.tick()" in rec and rec.index("nfl_props_log.tick()") < rec.index("dfs_field_capture.tick()")
       and 'errlog.note("MREC-fieldcap", _e)' in rec)
    src = open(os.path.join(ROOT, "dfs_field_capture.py")).read()
    ck("FC17 the capture loads the store only after its network reads (the prop log's pass must not be overwritten by "
       "a copy read minutes earlier) and ledgers every failure under a stable id",
       src.index("deep_cache.load(key)") > src.index("espn_rows(int(season), int(week))")
       and src.index("deep_cache.load(key)") > src.index("dk._get(dk._DRAFTABLES")
       and all(i in src for i in ('"FIELDCAP-dk"', '"FIELDCAP-espn"', '"FIELDCAP-snapshot"', '"FIELDCAP-group"')))

    # ---- the protocol is the file that was registered ---------------------------------------------------
    proto = os.path.join(ROOT, "research", "reports", "cl_field2_protocol.md")
    reg = os.path.join(ROOT, "research", "data", "cl_field2", "protocol.json")
    if os.path.exists(proto) and os.path.exists(reg):
        with open(reg) as f:
            R = json.load(f)
        with open(proto, "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()
        ck("FC18 the prospective protocol is byte for byte the file that was registered (its sha256 beside it); an "
           "edit after registration fails here",
           R.get("protocol_sha256") == h, f"{h} vs {R.get('protocol_sha256')}")
        from research import cl_field2_fields as CF
        fz = R.get("frozen") or {}
        ck("FC19 the candidates the harness builds are the registered ones: the module's constants equal the "
           "registration's, so a constant edited after the fact fails here",
           fz == CF.FROZEN, f"{fz} vs {CF.FROZEN}")
        ck("FC20 the owner's five criteria are dk_mech's, unchanged: the harness judges with dk_mix_classic's own "
           "thresholds (10 of 20 in the top 200, median ratio 0.5-2, collision 2x, 5 pp, 2x, 0.5 pp, $200, 2 pp)",
           CF.OWNER is __import__("research.dk_mix_classic", fromlist=["OWNER"]).OWNER
           and CF.OWNER == {"recall_top": 200, "recall_min_of_20": 10, "median_ratio": (0.5, 2.0), "collision_factor": 2.0,
                            "unique_pp": 5.0, "mass_factor": 2.0, "ownership_pp": 0.5, "salary_usd": 200.0,
                            "category_pp": 2.0}, str(CF.OWNER))
        ck("FC21 weeks 10 and 11 can never be read as validation: the harness refuses every contest in its "
           "development list",
           set(CF.DEVELOPMENT) >= {197645153, 197882014} and "DEVELOPMENT" in open(
               os.path.join(ROOT, "research", "cl_field2_fields.py")).read().split("def run_validate")[1][:1500])
    else:
        ck("FC18 the prospective protocol and its registration exist", False, "missing cl_field2_protocol.md or protocol.json")


if __name__ == "__main__":
    P, Fl = [], []

    def _ck(name, cond, detail=""):
        (P if cond else Fl).append(name)
        print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  [{detail}]"))
    run(_ck)
    print(f"RESULT: {len(P)} passed, {len(Fl)} failed")
    sys.exit(1 if Fl else 0)
