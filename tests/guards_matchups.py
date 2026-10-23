"""Guards for the Matchups tab's plumbing (matchups.py, /api/matchups, static/matchups.js):
the slate is published as an index plus one piece per game, a past date is never rebuilt,
the route validates what it is handed, and the tab renders the engines' payload without
an inline handler. Hermetic: boardshare is pointed at a temporary directory, no engine
runs, nothing is fetched.

    python3 tests/guards_matchups.py       # exits 1 on any failed check
    from tests.guards_matchups import run  # run(ck) inside the main suite
"""
import inspect
import json
import math
import os
import re
import sys
import tempfile
import time

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)


def _line(k, p, market=False):
    mk = {"ticker": f"KXNBAPTS-T{k}", "yes_bid": 40, "yes_ask": 44, "no_bid": 55, "no_ask": 60, "ts": 1.0,
          "volume": 9, "oi": 3, "fee_type": "quadratic"} if market else None
    val = {"best_side": "yes", "ev_yes": 1.5, "ev_no": -3.0, "ev_interval": {"yes": [-0.5, 3.0], "no": [-5, -1]},
           "roi_yes": 0.03, "roi_no": -0.05, "edge": 0.02, "p_market": 0.42, "market_kind": "mid",
           "fee_assumed": False, "believable": True, "quote_age_s": 30, "book": {"kind": "two_sided"},
           "price_used": {"yes": 44, "no": 60}} if market else None
    return {"k": k, "p": p, "p_lo": p - 0.03, "p_hi": p + 0.03, "hit_all": {"hits": 3, "n": 9},
            "hit_recent": {"hits": 1, "n": 5}, "hit_similar_def": {"hits_w": 2.0, "n_eff": 4.4},
            "market": mk, "value": val, "labels": ["positive_ev"] if market else [], "fit_side": "yes"}


def _payload():
    def pl(pid, gid):
        return {"player_id": pid, "name": f"P{pid}", "team": "AAA", "opp": "BBB", "game_id": gid, "pos": "G",
                "status": {"value": "active", "source": "t", "ts": None}, "role": {"usage": {"value": 0.2, "unit": "share"}},
                "opponent_profile": {"rim": {"value": 1.1, "unit": "x_league"}},
                "factors": [{"factor": "points: Pace", "effect": {"stat": "pts", "delta_mean": 0.1, "weight": "shipped"}}],
                "markets": [{"stat": "pts", "baseline_mean": 20.0, "adjusted_mean": 21.0, "dist": {"family": "x"},
                             "lines": [_line(10, 0.995), _line(20, 0.5, market=True), _line(30, 0.2), _line(45, 0.004)]}],
                "comparables": [{"date": "2026-01-01"}], "notes": ["n"], "minutes": {"projected": 30.0, "nodes": [1, 2]}}
    return {"sport": "nba", "built_ts": 5.0, "as_of": "games through 2026-10-22", "provenance": [{"source": "s"}],
            "games": [{"game_id": "11", "home": "AAA", "away": "BBB"}, {"game_id": "22", "home": "CCC", "away": "DDD"}],
            "notes": ["held-out market test"], "definitions": {"role": "d"}, "timing_s": 1.0,
            "players": [pl("1", "11"), pl("2", "11"), pl("3", "22")]}


def run(ck):
    import boardshare
    import matchups
    saved_dir, saved_put = boardshare._DIR, boardshare.put
    tmp = tempfile.mkdtemp(prefix="mx_guard_")
    boardshare._DIR = tmp
    try:
        order = []

        def rec_put(name, payload, age=0):
            order.append(name)
            return saved_put(name, payload, age)
        boardshare.put = rec_put
        P = _payload()
        idx = matchups.publish("nba", "2026-10-23", P)
        boardshare.put = saved_put
        g11, _a = matchups.game("nba", "2026-10-23", "11")
        disk_idx, _a2 = boardshare.get(matchups.index_name("nba", "2026-10-23"), None)
        ln = idx["players"][0]["markets"][0]["lines"]
        ck("matchups: a slate is published as one piece per game (the full contract for its players) and an index, the "
           "pieces FIRST so an index never names a game whose piece is missing",
           order == ["matchups_nba_2026-10-23_g11", "matchups_nba_2026-10-23_g22", "matchups_nba_2026-10-23"]
           and [p["player_id"] for p in g11["players"]] == ["1", "2"] and g11["players"][0]["factors"]
           and g11["game_id"] == "11" and g11["notes"] == ["held-out market test"] and disk_idx == idx,
           str(order))
        ck("matchups: the index carries no matrix, profiles or comparables -- only what the scanner reads -- and keeps the "
           "slate notes (the engine's record against the market) and the list of pieces",
           all(set(p) <= {"player_id", "name", "team", "opp", "game_id", "pos", "status", "minutes", "markets"}
               for p in idx["players"])
           and idx["notes"] == ["held-out market test"] and idx["pieces"] == ["11", "22"]
           and idx["players"][0]["minutes"] == {"projected": 30.0},
           str(sorted(idx["players"][0])))
        ck("matchups: an index line keeps the probability, band, quote, the EV block the tab reads and the labels with "
           "the fit's side; it drops hit tables and order-book detail; a line Kalshi does not list is kept only while it "
           "says something (2% to 98%), every line Kalshi lists is kept",
           [x["k"] for x in ln] == [20, 30]
           and ln[0]["market"] == {"ticker": "KXNBAPTS-T20", "yes_bid": 40, "yes_ask": 44, "no_bid": 55, "no_ask": 60, "ts": 1.0}
           and set(ln[0]["value"]) == set(matchups._VALUE_KEYS) and ln[0]["fit_side"] == "yes"
           and ln[0]["hit_similar_def"] == {"n_eff": 4.4} and "hit_all" not in ln[0]
           and len(g11["players"][0]["markets"][0]["lines"]) == 4,
           str(ln))
        e_idx = matchups.index_of({"sport": "nfl", "players": [], "error": "no committed model (coefs.json)",
                                   "slate": "the games on 2026-10-25"})
        ck("matchups: an engine that cannot build says why in `error`; the index carries it as a slate note (the tab "
           "reads notes) -- never an empty slate that says nothing",
           e_idx["notes"] == ["no committed model (coefs.json)"] and e_idx["slate"] == "the games on 2026-10-25"
           and e_idx["players"] == [], str(e_idx))
        rec_p = {"players": [{"markets": [
            {"stat": "pts", "lines": [{"labels": ["positive_ev"]}, {"labels": ["high_probability"]}]},
            {"stat": "reb", "lines": [{"labels": ["positive_ev", "positive_ev_robust"]}]},
            {"stat": "pra", "lines": [{"labels": ["positive_ev"]}]}]}]}
        n_marked = matchups.mark_record(rec_p, {"pts": {"model_ll": 0.58, "market_ll": 0.57, "n": 9, "season": "s"},
                                                "reb": {"model_ll": 0.55, "market_ll": 0.56, "n": 9, "season": "s"},
                                                "*": {"model_ll": 0.556, "market_ll": 0.542, "n": 9, "season": "s"}})
        ms = rec_p["players"][0]["markets"]
        nba_rec, nfl_rec = matchups.market_record("nba"), matchups.market_record("nfl")
        ck("matchups: a +EV line in a stat whose held-out record has Kalshi's price beating the model carries "
           "'market_better' (a stat without its own record reads the fallback); a stat the model forecast better, and "
           "a line that is not +EV, are untouched; the record rides on the payload",
           n_marked == 2 and ms[0]["lines"][0]["labels"] == ["positive_ev", "market_better"]
           and ms[0]["lines"][1]["labels"] == ["high_probability"]
           and ms[1]["lines"][0]["labels"] == ["positive_ev", "positive_ev_robust"]
           and ms[2]["lines"][0]["labels"] == ["positive_ev", "market_better"] and "market_record" in rec_p,
           str(ms))
        ck("matchups: the records are read from the files that produced the reports, ONE population per sport -- NBA "
           "2025-26 tight books (<= 10c) for every stat (Kalshi 0.542 vs model 0.556; the by-stat figures were all "
           "books, where the two tie), NFL 2026 frozen lines by stat with all rungs as fallback (0.432 vs 0.456); "
           "each record names its basis",
           abs(nba_rec["*"]["market_ll"] - 0.54238) < 1e-9 and abs(nba_rec["*"]["model_ll"] - 0.55616) < 1e-9
           and set(nba_rec) == {"*"} and "tight" in nba_rec["*"]["basis"] and all(r.get("basis") for r in nfl_rec.values())
           and abs(nfl_rec["*"]["market_ll"] - 0.4322) < 5e-4 and abs(nfl_rec["*"]["model_ll"] - 0.4563) < 5e-4
           and nfl_rec["*"]["season"] == "2026" and {"rec_yd", "rush_yd", "pass_yd", "rec", "td"} <= set(nfl_rec),
           str((nba_rec, nfl_rec)))
        ck("matchups: only today and tomorrow (Mountain time) are ever built; a past date (its book already holds that "
           "night's games) and a date two days out are not",
           matchups.buildable("2026-10-23", today="2026-10-23") and matchups.buildable("2026-10-24", today="2026-10-23")
           and not matchups.buildable("2026-10-22", today="2026-10-23")
           and not matchups.buildable("2026-10-25", today="2026-10-23") and not matchups.buildable("x", today="2026-10-23"))
        calls = []
        saved_nb, saved_today = boardshare.nonblocking, matchups.today_mt
        boardshare.nonblocking = lambda *a, **k: calls.append(a) or None
        matchups.today_mt = lambda: "2026-10-25"
        try:
            past, _age, building = matchups.board("nba", "2026-10-23")
        finally:
            boardshare.nonblocking, matchups.today_mt = saved_nb, saved_today
        ck("matchups: a past date serves what was published at the time and never starts a build",
           calls == [] and past == idx and building is False)
        for i in range(10):
            matchups._MEM[f"matchups_nba_2026-11-{i:02d}"] = (0.0, {})
        boardshare.nonblocking = lambda *a, **k: None
        matchups.today_mt = lambda: "2026-10-23"
        try:
            matchups.board("nba", "2026-10-23")
        finally:
            boardshare.nonblocking, matchups.today_mt = saved_nb, saved_today
        ck("matchups: the per-worker cache is pruned to the last few slates (a cache keyed on dates that never repeat "
           "grows forever otherwise)",
           len([k for k in matchups._MEM if isinstance(k, str)]) <= matchups._MEM_KEEP)
        src_board = inspect.getsource(matchups.board) + inspect.getsource(matchups._build)
        ck("matchups: the web worker never imports an engine -- produce() runs only in the niced child, under the "
           "heavy-build gate",
           "nba_matchup" not in src_board and "nfl_matchup" not in src_board
           and "with deep_cache.HEAVY_BUILD:" in inspect.getsource(matchups._build)
           and "os.nice(10)" in inspect.getsource(matchups.build_isolated)
           and "matchups._child(" in inspect.getsource(matchups.build_isolated)
           and "with_index=False" in inspect.getsource(matchups._child))

        # ---- the integration review's fixes (F2, F3, F4, F5, F7, F14, F19, F20) ----
        empty = {"sport": "nba", "players": [], "notes": ["no matchup book for 2026 yet (the nba_matchup job has not run)"]}
        full_idx = matchups.index_of(_payload())
        saved_bi = matchups.build_isolated
        import pc_worker as _pcw
        launches = []
        matchups.build_isolated = lambda sport, key, timeout=240: launches.append(key) or dict(empty)
        try:
            n0 = matchups._build("nba", "2026-10-24")
            fresh_e, _ = boardshare.get(matchups.index_name("nba", "2026-10-24"), matchups.TTL)
            any_e, age_e = boardshare.get(matchups.index_name("nba", "2026-10-24"), None)
            boardshare.put(matchups.index_name("nba", "2026-10-26"), full_idx)
            n1 = matchups._build("nba", "2026-10-26")
            kept, _ = boardshare.get(matchups.index_name("nba", "2026-10-26"), None)
            pc = matchups.pc_board("nba", "2026-10-27")
            pc_disk, _ = boardshare.get(matchups.index_name("nba", "2026-10-27"), None)
        finally:
            matchups.build_isolated = saved_bi
        ck("matchups (F2): an EMPTY slate (no book on the machine yet) never out-freshens a full one -- on the server it "
           "is published backdated (fresh for two minutes, then rebuilt) and never over a full board for the date; the "
           "PC's builder publishes nothing at all and answers a marker, never None (which _task_boards would re-invoke)",
           n0 is None and fresh_e is not None and any_e is not None
           and matchups.TTL - matchups._RETRY_S - 5 <= age_e <= matchups.TTL - matchups._RETRY_S + 30
           and n1 is None and kept == full_idx
           and isinstance(pc, dict) and "skipped" in pc and pc_disk is None
           and '_add("matchups-nba", lambda: __import__("matchups").pc_board("nba"))' in inspect.getsource(_pcw)
           and '_add("matchups-nfl", lambda: __import__("matchups").pc_board("nfl"))' in inspect.getsource(_pcw),
           str((n0, age_e, pc)))
        # a failing child: the next visits inside two minutes do not relaunch it, and say why
        matchups._MEM.pop(("_mx_fail", matchups.index_name("nba", "2026-10-28")), None)
        boardshare.put(matchups.index_name("nba", "2026-10-28"), full_idx, age=matchups.TTL + 5)   # a stale full board
        fails, built = [], []
        matchups.build_isolated = lambda sport, key, timeout=240: fails.append(key) and None
        saved_today = matchups.today_mt
        matchups.today_mt = lambda: "2026-10-28"

        def nb(name, ttl, mem, key, build, note_id=None):
            built.append(name)
            build()
            return None
        boardshare.nonblocking = nb
        try:
            r1 = matchups.board("nba", "2026-10-28")
            r2 = matchups.board("nba", "2026-10-28")
            r3 = matchups.board("nba", "2026-10-28")
            matchups.build_isolated = lambda sport, key, timeout=240: None
            matchups._MEM.pop(("_mx_fail", matchups.index_name("nba", "2026-10-29")), None)
            matchups.today_mt = lambda: "2026-10-29"
            r4 = matchups.board("nba", "2026-10-29")
            r5 = matchups.board("nba", "2026-10-29")
        finally:
            matchups.build_isolated, matchups.today_mt, boardshare.nonblocking = saved_bi, saved_today, saved_nb
        ck("matchups (F3): a failed build is not relaunched on every visit -- the next visits inside two minutes serve "
           "what is on disk (the stale full board) with building False; with no board at all they serve a placeholder "
           "whose note names the failure (ledger MX-child)",
           len(fails) == 1 and built == [matchups.index_name("nba", "2026-10-28"), matchups.index_name("nba", "2026-10-29")]
           and r1[0] == full_idx and r2[0] == full_idx and r3[2] is False
           and r4[0] is not None and r4[0]["players"] == [] and "MX-child" in r4[0]["notes"][0] and r5[2] is False,
           str((fails, built, r4)))
        saved_season = matchups._in_season
        matchups._in_season = lambda sport: False
        matchups.today_mt = lambda: "2026-10-30"
        boardshare.nonblocking = lambda *a, **k: built.append("OFF") or None
        try:
            off = matchups.board("nfl", "2026-10-30")
            off_pc = matchups.pc_board("nfl", "2026-10-30")
        finally:
            matchups._in_season, matchups.today_mt, boardshare.nonblocking = saved_season, saved_today, saved_nb
        # RC3: `building` is the worker's real in-flight flag; F10: an engine's error is not noted twice
        flight_name = matchups.index_name("nba", "2026-11-03")
        matchups.today_mt = lambda: "2026-11-03"
        boardshare.nonblocking = lambda *a, **k: None
        try:
            matchups._MEM[("_bs_flight", flight_name)] = (time.time(), True)
            b_on = matchups.board("nba", "2026-11-03")[2]
            matchups._MEM.pop(("_bs_flight", flight_name), None)
            b_off = matchups.board("nba", "2026-11-03")[2]
        finally:
            matchups.today_mt, boardshare.nonblocking = saved_today, saved_nb
        dup = matchups.index_of({"players": [], "error": "no committed model",
                                 "notes": ["The NFL matchup board could not be built: no committed model"]})
        ck("matchups (RC3/F10): `building` says True only while this worker's rebuild is in flight; an engine whose "
           "notes already carry its error does not get it a second time",
           b_on is True and b_off is False and dup["notes"] == ["The NFL matchup board could not be built: no committed model"],
           str((b_on, b_off, dup["notes"])))
        ck("matchups (F14): out of season nothing is built -- the server answers a one-note slate, the PC skips",
           "OFF" not in built and off[0]["players"] == [] and "out of season" in off[0]["notes"][0]
           and off_pc == {"skipped": "out of season"}, str((off, off_pc)))
        nog = _payload()
        nog["players"][2]["game_id"] = None
        nog_idx = matchups.publish("nba", "2026-10-31", nog)
        ck("matchups (F5): a player without a game id leaves the slate (his piece could never be read) and the drop is "
           "ledgered MX-nogame; no _gNone piece is written",
           [p["player_id"] for p in nog_idx["players"]] == ["1", "2"] and nog_idx["pieces"] == ["11"]
           and not os.path.exists(os.path.join(tmp, "matchups_nba_2026-10-31_gNone.pkl")), str(nog_idx["pieces"]))
        saved_rec = matchups.market_record
        matchups.market_record = lambda sport: {"*": {"model_ll": 0.6, "market_ll": 0.5, "n": 5, "season": "t", "basis": "b"}}
        try:
            mb_idx = matchups.publish("nba", "2026-11-01", _payload())
        finally:
            matchups.market_record = saved_rec
        mb_ln = [ln for ln in mb_idx["players"][0]["markets"][0]["lines"] if "positive_ev" in ln["labels"]]
        matchups.market_record = lambda sport: {}
        try:
            nr_idx = matchups.publish("nba", "2026-11-02", _payload())
        finally:
            matchups.market_record = saved_rec
        ck("matchups (F19/F20): publish applies the record -- a +EV line in a stat the market forecast better carries "
           "'market_better' in the PUBLISHED index -- and a sport whose record file cannot be read says so on the slate "
           "rather than leaving every +EV unqualified in silence",
           mb_ln and all("market_better" in ln["labels"] for ln in mb_ln) and mb_idx.get("market_record")
           and any("could not be read" in n for n in nr_idx["notes"]), str((mb_ln, nr_idx["notes"])))
        import types
        f4 = []
        fake4 = types.SimpleNamespace(
            _current_season=lambda: 2026, SCHEMA=2, load_season=lambda s: None,
            default_seasons=lambda cur, month: [cur - 1, cur],
            refresh=lambda seasons=None, keep_raw=True, log=None: f4.append((list(seasons), keep_raw)) or {})
        saved_mod4 = sys.modules.get("nfl_matchup_data")
        sys.modules["nfl_matchup_data"] = fake4
        try:
            matchups.nfl_data_job()
        finally:
            if saved_mod4 is not None:
                sys.modules["nfl_matchup_data"] = saved_mod4
            else:
                sys.modules.pop("nfl_matchup_data", None)
        ck("nfl_matchup job (F4): before October, with both stores missing, the season the daily rule reads anyway is "
           "not backfilled too (it would be built and fetched twice, its raw files kept the second time)",
           f4 == [([2024], False), ([2025, 2026], True)], str(f4))

        import app as A
        cl = A.app.test_client()
        saved_today = matchups.today_mt
        matchups.today_mt = lambda: "2026-10-25"
        try:
            r_bad = cl.get("/api/matchups?sport=nba&date=2026-13-40")
            r_game = cl.get("/api/matchups?sport=nba&date=2026-10-23&game=11")
            r_gbad = cl.get("/api/matchups?sport=nba&date=2026-10-23&game=11%3Bx")
            r_idx = cl.get("/api/matchups?sport=nba&date=2026-10-23")
            r_none = cl.get("/api/matchups?sport=nba&date=2026-10-20")
            r_sport = cl.get("/api/matchups?sport=mlb")
        finally:
            matchups.today_mt = saved_today
        jg, ji, jn = r_game.get_json(), r_idx.get_json(), r_none.get_json()
        ck("/api/matchups: a malformed date or game id is refused (400), never handed to a builder or a file name; an "
           "unknown sport is refused",
           r_bad.status_code == 400 and r_gbad.status_code == 400 and r_sport.status_code == 400)
        ck("/api/matchups: ?game= serves that game's full players; the slate URL serves the index; a past date with "
           "nothing published says so and is not 'building'",
           jg["status"] == "ok" and len(jg["players"]) == 2 and jg["players"][0]["factors"]
           and ji["status"] == "ok" and "factors" not in ji["players"][0] and ji["building"] is False
           and jn["status"] == "none" and jn["building"] is False and "2026-10-20" in jn["why"],
           str((jg.get("status"), ji.get("status"), jn)))
        ck("/api/matchups: NaN and inf leave as null (Flask writes a bare NaN, which no browser parses)",
           A._json_finite({"a": [float("nan"), 1.0, {"b": float("inf")}]}) == {"a": [None, 1.0, {"b": None}]}
           and math.isnan(float("nan")))
    finally:
        boardshare._DIR, boardshare.put = saved_dir, saved_put
        for k in [k for k in matchups._MEM if (isinstance(k, str) and k.startswith("matchups_nba_2026-1"))
                  or (isinstance(k, tuple) and k[0] == "_mx_fail")]:
            matchups._MEM.pop(k, None)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    js = open(os.path.join(_ROOT, "static", "matchups.js")).read()
    html = open(os.path.join(_ROOT, "templates", "index.html")).read()
    sw = open(os.path.join(_ROOT, "static", "sw.js")).read()
    ck("Matchups tab: no inline event handler anywhere -- one delegated listener reads data-mx attributes (an id in "
       "onclick=\"...\" is decoded before the script runs; the Fable review ran an alert() through one)",
       not re.search(r"\bon[a-z]+\s*=", "\n".join(
           ln for ln in js.splitlines() if not ln.lstrip().startswith("//")))
       and 'root.addEventListener("click", _mxClick)' in js and 'root.addEventListener("change", _mxChange)' in js)
    ck("Matchups tab: the player page fetches its game's piece (&game=), the slate's notes are drawn above everything, "
       "a factor held at weight 0 is folded away and never shown as applied, and 'Strong fit' names its side",
       "&game=${encodeURIComponent(g)}" in js and "(d.notes || []).map(" in js
       and "function _mxZero(f)" in js and "not applied" in js
       and 'lab === "strong_fit") side = _mxSide(l.fit_side)' in js
       and "market_better: [" in js)
    # F1: a side is whitelisted, never written raw -- source check, then the real functions under node
    raw_side = [m for m in re.findall(r"\$\{(?:side|b\.side)[^}]*\}", js) if "_mxEsc(" not in m]
    node_ok, node_out = None, ""
    try:
        import shutil
        import subprocess
        if shutil.which("node"):
            harness = ("const $ = () => null;\n" + js + "\n"
                       "const evil = '<img src=x onerror=alert(9)>';\n"
                       "const l = {p: 0.8, labels: ['positive_ev', 'strong_fit', 'high_probability', 'market_better'],"
                       " fit_side: evil, value: {best_side: evil, ev_yes: 3, ev_no: -2, ev_interval: {yes: [1, 5]}, believable: true}};\n"
                       "const b = _mxBest(l.value, l.labels);\n"
                       "process.stdout.write(JSON.stringify([_mxChips(l, 'pts'), _mxEvCell(b, true), b.mkt]));")
            r = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=30)
            out = json.loads(r.stdout) if r.returncode == 0 and r.stdout else None
            node_out = r.stdout[:600] + r.stderr[-300:]
            node_ok = bool(out) and all("<img" not in x.lower() for x in out[:2]) and out[2] is True \
                and "<b>" not in out[1] and "market better on record" in out[1]
    except Exception as e:          # node present but the harness broke: that is a failure, not a skip
        node_ok, node_out = False, repr(e)
    ck("Matchups tab (F1): a side ('YES' / 'NO') is whitelisted, never an engine string written into the page -- no "
       "raw ${side} / ${b.side} interpolation in the source, and (where node exists) the real _mxChips / _mxEvCell "
       "given best_side / fit_side = '<img src=x onerror=...>' emit no tag",
       not raw_side and "function _mxSide(x)" in js and node_ok is not False, str((raw_side, node_ok, node_out)))
    ck("Matchups tab (F18/F17): a line the record impeaches (market_better) has its EV muted, never bold, and ranked "
       "with the thin books; the chip's tooltip prints the record it rests on (season, n, both log losses)",
       "mkt: (labels || []).includes(\"market_better\")" in js and "r.b && r.b.ok && !r.b.mkt" in js
       and "(market better on record)" in js and "function _mxRecordText(stat)" in js
       and "_mxChips(r.l, r.m.stat)" in js and "_mxChips(l, m.stat)" in js)
    ck("Matchups tab (F3/F6/F9/F15): retries stop with an honest sentence; a player not on the slate says so instead "
       "of loading forever; an empty table counts what the 'only lines Kalshi lists' tick hides; a new sport or date "
       "clears the previous board's status",
       "Still building after eight checks" in js and "He is not on this slate" in js
       and "_mxOk(d.age_s) && !d.placeholder" in js
       and "function _mxBand(l)" in js and js.count("${_mxBand(") == 3 and "l.p_hi > l.p_lo" in js
       and ': "no band"; }' in js
       and js.count("${_mxPct(l.p_lo)}") == 1 and "${_mxPct(r.l.p_lo)}" not in js
       and 'hidden by "only lines Kalshi lists"' in js and "if (!retry) {" in js
       and "setTimeout(() => mxLoad(true), 20000)" in js)
    ck("Matchups tab: EV off a book prop_value does not believe is muted and ranks below every believable line; a "
       "building board is retried a bounded number of times",
       "ok: v.believable !== false" in js and "(thin book)" in js and "_mx.tries < 8" in js)
    ck("Matchups tab is wired: the tab button, its root, the script (cache-busted), the service worker's precache and "
       "the asset version",
       'data-tab="matchups"' in html and 'id="mxRoot"' in html and "/static/matchups.js?v={{ v }}" in html
       and '"/static/matchups.js"' in sw and '"matchups.js"' in inspect.getsource(A._asset_version))
    import artifacts
    import pc_worker
    art = open(os.path.join(_ROOT, "artifacts.py")).read()
    ck("the NBA engine's stores ride the artifact contract: SCHEMA 6 names the deep keys and the boards; the PC runs "
       "the nba_matchup job and builds the NBA board; the server registers the job PC-first",
       artifacts.SCHEMA >= 6 and "nba_matchup_book_<season>.pkl" in art and "matchups_<sport>_<date>_g<id>.pkl" in art
       and '("nba_matchup", lambda: _plain("nba_matchup"' in inspect.getsource(pc_worker)
       and '_add("matchups-nba", lambda: __import__("matchups").pc_board("nba"))' in inspect.getsource(pc_worker)
       and 'deep_cache.register("nba_matchup", run_nba_matchup, max_age=6 * 3600, cadence="age")' in inspect.getsource(A)
       and '_deep_pc_first("nba_matchup")' in inspect.getsource(A))
    json.dumps(idx)            # the index is plain JSON

    # the NFL data job backfills the two seasons the profiles read back, once
    import types
    calls = []
    fake = types.SimpleNamespace(
        _current_season=lambda: 2026, SCHEMA=2,
        load_season=lambda s: {"schema": 2} if s == 2025 else None,
        default_seasons=lambda cur, month: [cur] if month >= 10 else [cur - 1, cur],
        refresh=lambda seasons=None, keep_raw=True, log=None: calls.append((list(seasons), keep_raw)) or {"seasons": {}})
    saved_mod = sys.modules.get("nfl_matchup_data")
    sys.modules["nfl_matchup_data"] = fake
    try:
        matchups.nfl_data_job()
        first = list(calls)
        fake.load_season = lambda s: {"schema": 2}
        calls.clear()
        matchups.nfl_data_job()
        second = list(calls)
    finally:
        if saved_mod is not None:
            sys.modules["nfl_matchup_data"] = saved_mod
        else:
            sys.modules.pop("nfl_matchup_data", None)
    import clock
    daily = [2026] if clock.today_et().month >= 10 else [2025, 2026]
    ck("nfl_matchup job: a season the profiles read back (two before the current) with no store is backfilled once, "
       "its raw files dropped (keep_raw False); the daily refresh follows; with both stores present only the daily runs",
       first == [([2024], False), (daily, True)] and second == [(daily, True)], str((first, second)))
    ck("nfl_matchup job is registered PC-first and runs on the server only in a niced child under the heavy-build gate",
       'deep_cache.register("nfl_matchup", run_nfl_matchup)' in inspect.getsource(A)
       and '_deep_pc_first("nfl_matchup")' in inspect.getsource(A)
       and 'matchups.job_isolated("matchups", "nfl_data_job")' in inspect.getsource(A)
       and '__import__("matchups").nfl_data_job(log=print)' in inspect.getsource(pc_worker)
       and "os.nice(10)" in inspect.getsource(matchups.job_isolated)
       and "nfl_matchup_<season>.pkl" in art)


def main():
    fails, n = [], [0]

    def ck(name, cond, detail=""):
        n[0] += 1
        if not cond:
            fails.append((name, detail))
            print(f"FAIL {name} {detail}")
    run(ck)
    print(f"guards_matchups: {n[0] - len(fails)}/{n[0]} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
