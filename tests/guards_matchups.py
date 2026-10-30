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


def _mx_sort_default_ok(js):
    return ('sort: "best"' in js and 'side: "best"' in js and '["best", "Best picks"]' in js
            and '["yes", "YES"], ["no", "NO"]' in js and "localStorage.setItem(\"mxSortSide\"" in js)


def _mx_fold_check(js):
    """The scanner's one-line-per-player fold, run on the real functions under node: (ok, out), ok None
    where node is absent. The owner (2026-10-30): "only the best option for a certain player shows once ...
    If it's 2 different slips like points and assists, keep those. But 2 of the same is annoying"."""
    fo_ok, fo_out = None, ""
    try:
        import shutil
        import subprocess
        if shutil.which("node"):
            h5 = ("const $ = () => null;\n" + js + "\n" + r"""
function L(k, p, lo, hi, evy, evn, ivy, ivn, labels) {
  return {k, p, p_lo: lo, p_hi: hi, market: {yes_ask: 50, no_ask: 52}, labels: labels || [], hit_similar_def: {n_eff: 12},
    value: {best_side: evy >= evn ? 'yes' : 'no', ev_yes: evy, ev_no: evn, ev_interval: {yes: ivy, no: ivn},
            believable: true, edge: 0.05, p_market: 0.5, market_kind: 'two_sided'}};
}
const ann = {player_id: '1', name: 'Ann', team: 'A', opp: 'B', game_id: '9', markets: [
  {stat: 'ast', lines: [
    L(4, 0.70, 0.60, 0.80, 9.0, -11, [-1, 19], [-21, -1], ['positive_ev']),
    L(6, 0.60, 0.58, 0.62, 6.0, -8, [4, 8], [-10, -6], ['positive_ev']),
    L(8, 0.45, 0.40, 0.50, -3.0, -5, [-8, 2], [-10, 0], ['strong_fit']),
    L(10, 0.10, 0.08, 0.12, -9.0, 5.0, [-11, -7], [3, 7], ['positive_ev'])]},
  {stat: 'pts', lines: [L(15, 0.55, 0.50, 0.60, 2.0, -4, [0.5, 3.5], [-6, -2], ['positive_ev'])]}]};
const bob = {player_id: '2', name: 'Bob', team: 'C', opp: 'D', game_id: '8', markets: [
  {stat: 'ast', lines: [L(4, 0.52, 0.50, 0.54, 1.0, -3, [0.2, 1.8], [-4, -2], ['positive_ev'])]}]};
const cy = {player_id: 'None', name: 'Cy', team: 'E', opp: 'F', game_id: '7', markets: [
  {stat: 'reb', lines: [L(5, 0.40, 0.35, 0.45, -2.0, -6, [-6, 2], [-9, -3], [])]}]};
const di = {player_id: 'None', name: 'Di', team: 'E', opp: 'F', game_id: '7', markets: [
  {stat: 'reb', lines: [L(5, 0.40, 0.35, 0.45, -2.5, -6, [-7, 1], [-9, -3], [])]}]};
const d = {status: 'ok', games: [], players: [ann, bob, cy, di]};
_mx.data = d; _mx.listed = false; _mx.side = 'best'; _mx.dir = -1; _mx.sort = 'best';
const rows = (html) => [...html.matchAll(/data-mx="open" data-val="[^"]*">([^<]*)<\/a>[\s\S]*?<td>([A-Za-z+ ]+?) (\d+)\+(?: <span[^>]*>\+(\d+) more<\/span>)?<\/td>/g)]
  .map((m) => `${m[1]} ${m[2]} ${m[3]}${m[4] ? ' +' + m[4] : ''}`);
const dflt = _mx.fold;
const h0 = mxScanView(d); const best = rows(h0);
_mx.sort = 'ev'; const ev = rows(mxScanView(d));
_mx.sort = 'p'; const p = rows(mxScanView(d));
_mx.sort = 'edge'; const edg = rows(mxScanView(d));
_mx.sort = 'best'; _mx.dir = 1; const asc = rows(mxScanView(d)); _mx.dir = -1;
_mx.sort = 'nef'; const nef = rows(mxScanView(d)); _mx.sort = 'best';
_mx.label = 'strong_fit'; const fit = rows(mxScanView(d)); _mx.label = '';
_mx.stat = 'ast'; const astOnly = rows(mxScanView(d)); _mx.stat = '';
_mx.fold = false; const h1 = mxScanView(d); const all = rows(h1);
const store = {}; globalThis.localStorage = {getItem: (k) => store[k] || null, setItem: (k, v) => { store[k] = v; }};
globalThis.mxRender = () => {};
const tick = (checked) => _mxChange({target: {closest: () => ({dataset: {mx: 'fold'}, checked})}});
_mx.fold = true; tick(false); const offAfter = _mx.fold; const savedOff = store.mxSortSide;
_mx.fold = true; _mxRestore(); const restoredOff = _mx.fold;
store.mxSortSide = '{"fold":"no"}'; _mx.fold = true; _mxRestore(); const keptBad = _mx.fold;
tick(true); const onAfter = _mx.fold;
process.stdout.write(JSON.stringify({dflt, best, ev, p, edg, asc, nef, fit, astOnly, all,
  note0: /3 other lines folded/.test(h0), note1: /folded/.test(h1),
  box0: h0.includes('data-mx="fold" checked'), box1: h1.includes('data-mx="fold"') && !h1.includes('data-mx="fold" checked'),
  offAfter, savedOff, restoredOff, keptBad, onAfter}));
""")
            r5 = subprocess.run(["node", "-e", h5], capture_output=True, text=True, timeout=30)
            o5 = json.loads(r5.stdout) if r5.returncode == 0 and r5.stdout else None
            fo_out = r5.stdout[:1200] + r5.stderr[-400:]
            tail = ["Ann Points 15", "Bob Assists 4", "Cy Rebounds 5", "Di Rebounds 5"]
            fo_ok = bool(o5) and o5["dflt"] is True \
                and o5["best"] == ["Ann Assists 6 +3"] + tail \
                and o5["ev"] == ["Ann Assists 4 +3"] + tail and o5["p"] == ["Ann Assists 10 +3"] + tail \
                and o5["edg"] == ["Ann Assists 6 +3"] + tail \
                and o5["asc"] == ["Di Rebounds 5", "Cy Rebounds 5", "Bob Assists 4", "Ann Points 15", "Ann Assists 6 +3"] \
                and o5["nef"] == ["Ann Assists 6 +3"] + tail and o5["fit"] == ["Ann Assists 8"] \
                and o5["astOnly"] == ["Ann Assists 6 +3", "Bob Assists 4"] \
                and o5["all"] == ["Ann Assists 6", "Ann Assists 10", "Ann Points 15", "Bob Assists 4", "Ann Assists 4",
                                  "Cy Rebounds 5", "Di Rebounds 5", "Ann Assists 8"] \
                and o5["note0"] and not o5["note1"] and o5["box0"] and o5["box1"] \
                and o5["offAfter"] is False and '"fold":false' in (o5["savedOff"] or "") \
                and o5["restoredOff"] is False and o5["keptBad"] is True and o5["onAfter"] is True
    except Exception as e:
        fo_ok, fo_out = False, repr(e)
    return fo_ok, fo_out


def _fake_d(calls, load_season, default_seasons=None, daily_raises=False, build_raises=()):
    """A stand-in nfl_matchup_data for the deep job: records each refresh as ("D", seasons, keep_raw).
    `build_raises`: seasons whose build fails AFTER their files were downloaded."""
    import types

    def refresh(seasons=None, keep_raw=True, log=None):
        calls.append(("D", list(seasons), keep_raw))
        if daily_raises and list(seasons) == [2026]:
            raise RuntimeError("daily refresh failed")
        _download(seasons, ("pbp", "participation", "ftn"), keep_raw or bool(set(seasons) & set(build_raises)))
        if set(seasons) & set(build_raises):
            raise RuntimeError("backfill build failed")
        return {"seasons": {}}
    return types.SimpleNamespace(
        _current_season=lambda: 2026, SCHEMA=2, load_season=load_season,
        default_seasons=default_seasons or (lambda cur, month: [cur] if month >= 10 else [cur - 1, cur]),
        refresh=refresh, url_for=lambda kind, season=None: f"https://x/{kind}_{season}.csv")


def _fake_r(calls, aux, fail=()):
    """A stand-in nfl_rush_data: records ("R", seasons, keep_raw); `aux` gives the stored aux per season."""
    import types

    def refresh(seasons=None, keep_raw=True, log=None):
        calls.append(("R", list(seasons), keep_raw))
        if set(seasons) & set(fail):
            _download(seasons, ("pfr_rush", "injuries", "snaps"), True)   # fetched, then the build failed
            raise ValueError("bad source file")
        _download(seasons, ("pfr_rush", "injuries", "snaps"), keep_raw)
        return {"ok": seasons[0]}
    return types.SimpleNamespace(
        load_aux=aux, refresh=refresh, url_for=lambda kind, season=None: f"https://x/{kind}_{season}.csv",
        aux_matches=lambda a, st: bool(a and st and a.get("store_built") == st.get("built")
                                       and a.get("store_n") == st.get("n")),
        aux_complete=lambda a: bool(a) and a.get("complete") is True)     # as strict as nfl_rush_data's


_LEDGER = {}


def _download(seasons, kinds, keep):
    """What a real refresh leaves in the raw dir: its downloads (and their freshness records) when it keeps them."""
    if not keep:
        return
    import deep_cache
    raw = os.path.join(deep_cache.CACHE_DIR, "nfl_matchup_raw")
    for s in seasons:
        for k in kinds:
            for suffix in ("", ".meta.json"):
                with open(os.path.join(raw, f"{k}_{s}.csv{suffix}"), "w") as fh:
                    fh.write("x")


def _ledger_has_any(code):
    """True / False when the real ledger can be read, None when it cannot (a guard never fails on that alone)."""
    try:
        import errlog
        return any(r.get("code") == code for r in errlog.recent(limit=200))
    except Exception:
        return None


def _ledger_has(code):
    return code in _LEDGER.get("codes", ())


def _run_job(fake_d, fake_r, plant=(), marks=None, store_dir=None):
    """Run matchups.nfl_data_job against the fakes in a scratch deep store with a scratch error ledger.
    `plant`: seasons whose raw files (and their freshness records) exist before the run; `marks` {season: days ago}
    a rushing-only backfill was last tried; `store_dir` reuses a scratch deep store across runs. Returns (result or the
    exception raised, files left in the raw dir, the planted files the job removed BEFORE its first refresh)."""
    import deep_cache
    import errlog
    import matchups
    tmp = store_dir or tempfile.mkdtemp(prefix="mx_job_")
    raw = os.path.join(tmp, "nfl_matchup_raw")
    os.makedirs(raw, exist_ok=True)
    kinds = ("pbp", "participation", "ftn", "pfr_rush", "pfr_def", "pfr_pass", "injuries", "snaps")
    for s in plant:
        for k in kinds:
            for suffix in ("", ".meta.json"):
                with open(os.path.join(raw, f"{k}_{s}.csv{suffix}"), "w") as fh:
                    fh.write("x")
    pre = []
    real_refresh = fake_d.refresh

    def watched_refresh(seasons=None, **kw):
        # every prior-season download on disk at the moment a prior season starts downloading
        if set(seasons) <= {2024, 2025}:
            pre.append(sorted(f for f in os.listdir(raw) if "_2024." in f or "_2025." in f))
        return real_refresh(seasons=seasons, **kw)
    fake_d.refresh = watched_refresh
    saved = {m: sys.modules.get(m) for m in ("nfl_matchup_data", "nfl_rush_data", "nfl_rush")}
    saved_dir, saved_db, saved_init = deep_cache.CACHE_DIR, errlog._DB, errlog._init_done
    if marks:
        import pickle as _pk
        for s_, days in marks.items():
            with open(os.path.join(tmp, f"nfl_rush_backfill_try_{s_}.pkl"), "wb") as fh:
                t_ = time.time() - days * 86400
                _pk.dump({"generated_at": t_, "payload": {"ts": t_}}, fh)     # deep_cache.save's record
    saved_throttle = (dict(errlog._last_write), dict(errlog._pending))
    errlog._last_write.clear()          # a note throttled by an earlier one would never reach this run's ledger
    errlog._pending.clear()
    sys.modules["nfl_matchup_data"] = fake_d
    sys.modules["nfl_rush_data"] = None if fake_r == "broken" else fake_r
    import types as _ty
    vec_calls = _LEDGER.setdefault("vectors", [])
    vec_calls.clear()

    def _vectors(log=None):
        vec_calls.append(1)
        if _LEDGER.get("vectors_raise"):
            raise RuntimeError("vectors failed")
        return {"vectors": 0}
    sys.modules["nfl_rush"] = _ty.SimpleNamespace(precompute_vectors=_vectors)
    deep_cache.CACHE_DIR = tmp
    errlog._DB, errlog._init_done = os.path.join(tmp, "errlog.db"), False
    try:
        try:
            res = matchups.nfl_data_job()
        except Exception as e:
            res = e
        _LEDGER["codes"] = {r.get("code") for r in errlog.recent(limit=50)}
    finally:
        for m, v in saved.items():
            if v is not None:
                sys.modules[m] = v
            else:
                sys.modules.pop(m, None)
        deep_cache.CACHE_DIR, errlog._DB, errlog._init_done = saved_dir, saved_db, saved_init
        errlog._last_write.clear()
        errlog._last_write.update(saved_throttle[0])
        errlog._pending.clear()
        errlog._pending.update(saved_throttle[1])
        fake_d.refresh = real_refresh
    # what is left of the PRIOR seasons' downloads (the daily season's are kept on purpose)
    left = sorted(f for f in os.listdir(raw) if "_2024." in f or "_2025." in f)
    return res, left, pre


def run(ck):
    import boardshare
    import matchups
    saved_dir, saved_put = boardshare._DIR, boardshare.put
    tmp = tempfile.mkdtemp(prefix="mx_guard_")
    boardshare._DIR = tmp
    # the board checks hold in every month: out of season (June to October for the NBA) the real
    # _in_season turned them into "out of season" placeholders and the suite red (found by the data
    # re-check with the clock patched to September); the out-of-season check sets its own
    saved_in_season = matchups._in_season
    matchups._in_season = lambda sport: True
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
        f4 = []
        fake4 = _fake_d(f4, load_season=lambda s: None, default_seasons=lambda cur, month: [cur - 1, cur])
        _run_job(fake4, _fake_r(f4, aux=lambda s: None))
        ck("nfl_matchup job (F4): before October, with both stores missing, the season the daily rule reads anyway is "
           "not backfilled too (it would be built and fetched twice, its raw files kept the second time)",
           [c for c in f4 if c[0] == "D"] == [("D", [2024], True), ("D", [2025, 2026], True)], str(f4))

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
        matchups._in_season = saved_in_season
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
    bd_ok, bd_out = None, ""
    try:
        import shutil
        import subprocess
        if shutil.which("node"):
            evil = "<img src=x onerror=alert(7)>"
            h2 = ("const $ = () => null;\n" + js + "\n"
                  "const p = {breakdown: [{label: '" + evil + "', value: 3.1, unit: '" + evil + "', kind: '" + evil + "',"
                  " basis: '" + evil + "'}, {label: 'carries', value: 14.2, unit: 'per_game', kind: 'modelled', basis: 'b'}, null]};\n"
                  "process.stdout.write(JSON.stringify([_mxBreakdown(p), _mxBreakdown({}), _mxBreakdown({breakdown: 'x'})]));")
            r2 = subprocess.run(["node", "-e", h2], capture_output=True, text=True, timeout=30)
            o2 = json.loads(r2.stdout) if r2.returncode == 0 and r2.stdout else None
            bd_out = r2.stdout[:400] + r2.stderr[-300:]
            bd_ok = bool(o2) and "<img" not in o2[0].lower() and "carries" in o2[0] and "14.20" in o2[0] \
                and o2[1] == "" and o2[2] == ""
    except Exception as e:
        bd_ok, bd_out = False, repr(e)
    ck("Matchups tab: a player's component breakdown (the rushing upgrade's designed runs / scrambles / kneels, "
       "carries, YBC vs YAC) renders every row through the escaped fact formatter; a hostile label, unit, kind or basis "
       "emits no tag; a missing or malformed breakdown renders nothing",
       "function _mxBreakdown(p)" in js and "${_mxBreakdown(p)}" in js and bd_ok is not False, str((bd_ok, bd_out)))
    # the owner's sort menu and side picker (2026-10-23)
    so_ok, so_out = None, ""
    try:
        import shutil
        import subprocess
        if shutil.which("node"):
            h3 = ("const $ = () => null;\n" + js + "\n"
                  "function L(k, p, lo, hi, evy, evn, ivy, ivn, believable, labels, edge, kind) { return {k, p, p_lo: lo, p_hi: hi,"
                  " market: {yes_ask: 50, no_ask: 52}, labels: labels || [], value: {best_side: evy >= evn ? 'yes' : 'no',"
                  " ev_yes: evy, ev_no: evn, ev_interval: {yes: ivy, no: ivn}, believable, edge, p_market: 0.4,"
                  " market_kind: kind || 'two_sided'}}; }\n"
                  "const lines = [\n"
                  " L(1, 0.70, 0.60, 0.80, 9.0, -11, [-1.0, 19], [-21, -1], true, ['positive_ev'], 0.20),\n"   # big EV, poor floor
                  " L(2, 0.60, 0.58, 0.62, 6.0, -8, [4.0, 8], [-10, -6], true, ['positive_ev'], 0.10),\n"     # robust: best pick
                  " L(3, 0.90, 0.88, 0.92, 30, -32, [28, 32], [-34, -30], false, ['thin_book'], 0.40),\n"     # thin book
                  " L(4, 0.80, 0.78, 0.82, 20, -22, [18, 22], [-24, -20], true, ['positive_ev', 'market_better'], 0.30),\n"
                  " L(5, 0.20, 0.15, 0.25, -12, 7.0, [-17, -7], [2, 12], true, ['positive_ev'], -0.30),\n"
                  " L(6, 0.45, 0.40, 0.50, -3.0, -5, [-8, 2], [-10, 0], true, [], -0.05),\n"                  # clean, -EV
                  " L(7, 0.75, 0.70, 0.80, 13.3, -15, [10, 16], [-18, -12], true, ['stale_quote', 'low_evidence'], 0.25),\n"
                  " L(8, 0.30, 0.25, 0.35, -9, 4.0, [-14, -4], [1, 9], true, ['positive_ev'], -0.20, 'one_sided'),\n"
                  " L(9, 0.50, 0.30, 0.70, 2.0, -4, [-30, 10], [-12, 28], false, ['thin_book'], 0.0)];\n"   # muted +EV, floor -30
                  "_mx.data = {status: 'ok', games: [], players: [{player_id: '1', name: 'P', team: 'A', opp: 'B', game_id: '9',"
                  " markets: [{stat: 'pts', lines}]}]};\n"
                  # every rung of the one ladder, the fold off: this check is the ORDER (the fold's own is below)
                  "_mx.listed = false; _mx.fold = false; _mx.sort = 'best'; _mx.side = 'best'; _mx.dir = -1;\n"
                  "const ord = (html) => [...html.matchAll(/Points (\\d+)\\+/g)].map((m) => +m[1]);\n"
                  "const best = ord(mxScanView(_mx.data));\n"
                  "_mx.sort = 'ev'; const ev = ord(mxScanView(_mx.data));\n"
                  "_mx.sort = 'best'; _mx.side = 'no'; const noHtml = mxScanView(_mx.data); const noOrd = ord(noHtml);\n"
                  "_mx.sort = 'p'; const noP = ord(mxScanView(_mx.data));\n"
                  "_mx.sort = 'edge'; const noEdge = ord(mxScanView(_mx.data));\n"
                  "_mx.sort = 'best'; _mx.label = 'positive_ev'; const noEvChip = ord(mxScanView(_mx.data)); _mx.label = '';\n"
                  "_mx.side = 'yes'; const yesOrd = ord(mxScanView(_mx.data));\n"
                  "const st7 = [_mxSideValue(lines[6].value, lines[6].labels, 'yes', 'pts').stale,"
                  " _mxSideValue(lines[6].value, lines[6].labels, 'no', 'pts').stale]; _mx.side = 'no';\n"
                  "const r5 = _mxSideP(lines[4], 'no');\n"
                  # the change handler and the stored choices, fed bad values
                  "const store = {}; globalThis.localStorage = {getItem: (k) => store[k] || null, setItem: (k, v) => { store[k] = v; }};\n"
                  "const ch = (act, value) => _mxChange({target: {closest: () => ({dataset: {mx: act}, value})}});\n"
                  "globalThis.mxRender = () => {};\n"
                  "ch('side', 'constructor'); const sideBad = _mx.side; ch('sortsel', '<img src=x>'); const sortBad = _mx.sort;\n"
                  "ch('side', 'no'); ch('sortsel', 'edge'); const saved = store.mxSortSide;\n"
                  "_mx.side = 'best'; _mx.sort = 'best'; store.mxSortSide = '{\"sort\":\"<img>\",\"side\":\"__proto__\"}'; _mxRestore();\n"
                  "const restoredBad = [_mx.sort, _mx.side]; store.mxSortSide = saved; _mxRestore();\n"
                  "process.stdout.write(JSON.stringify({best, ev, noOrd, noP, noEdge, noEvChip, yesOrd, st7, r5,"
                  " vs: _mxVsMkt(lines[4].value, 'no'),"
                  " vs1: _mxVsMkt(lines[7].value, 'no'), sideBad, sortBad, saved, restoredBad, restored: [_mx.sort, _mx.side],"
                  " sel: noHtml.includes('data-mx=\"sortsel\"') && noHtml.includes('data-mx=\"side\"')}));")
            r3 = subprocess.run(["node", "-e", h3], capture_output=True, text=True, timeout=30)
            o3 = json.loads(r3.stdout) if r3.returncode == 0 and r3.stdout else None
            so_out = r3.stdout[:900] + r3.stderr[-400:]
            # Best picks: clean +EV by cautious EV (2: +4, 5: NO +2, 8: NO +1, 1: -1), then the muted +EV
            # (thin 3: 28, impeached 4: 18, stale 7: 10, thin 9: -30 -- still above the -EV tier), then the
            # negative-EV line 6 (cautious -8). Highest EV: every
            # believable, fresh, unimpeached line by EV (1, 5, 2, 8, then 6 at -3), the muted ones after.
            so_ok = bool(o3) \
                and o3["best"] == [2, 5, 8, 1, 3, 4, 7, 9, 6] \
                and o3["ev"] == [1, 5, 2, 8, 6, 3, 4, 7, 9] \
                and o3["noOrd"][:2] == [5, 8] and o3["noOrd"][-4:] == [7, 1, 4, 3] \
                and o3["noP"] == [5, 8, 6, 9, 2, 1, 7, 4, 3] \
                and o3["noEdge"] == [5, 6, 9, 2, 1, 7, 4, 3, 8] \
                and o3["noEvChip"] == [5, 8] \
                and o3["yesOrd"] == [2, 1, 3, 4, 7, 9, 6, 8, 5] and o3["st7"] == [True, False] \
                and abs(o3["r5"]["p"] - 0.80) < 1e-9 and abs(o3["r5"]["lo"] - 0.75) < 1e-9 \
                and abs(o3["r5"]["hi"] - 0.85) < 1e-9 and "+30.0 pts" in o3["vs"] and "mkt 60%" in o3["vs"] \
                and o3["vs1"] == "-" and o3["sideBad"] == "best" and o3["sortBad"] == "best" \
                and o3["restoredBad"] == ["best", "best"] and o3["restored"] == ["edge", "no"] and o3["sel"]
    except Exception as e:
        so_ok, so_out = False, repr(e)
    ck("Matchups tab (sort / side): 'Best picks' (the default) ranks believable +EV lines by the EV at the cautious end "
       "of the band (a +6c line whose floor is +4c above a NO +7c whose floor is +2c above a +9c line whose floor is "
       "-1c), thin books and lines the record "
       "impeaches next (with a stale quote), then every negative-EV line; 'Highest EV' ranks by EV in the same tiers; "
       "the NO side shows 1 - p with the band flipped, NO's EV and the edge negated against 1 - p_market, a one-sided "
       "book's NO edge as '-' (no NO contract behind it); the probability and edge sorts read the NO side; the +EV chip "
       "with NO chosen keeps only lines whose NO EV is positive; with YES chosen a stale quote's +EV ranks after the "
       "clean +EV lines (sort review R2), and the stale qualifier rides only the side whose +EV earned it (R1); the "
       "menus and the stored choices refuse bad values",
       _mx_sort_default_ok(js) and so_ok is not False, str((so_ok, so_out)))
    # The owner (2026-10-30): one row per player and stat -- "only the best option for a certain player shows
    # once ... If it's 2 different slips like points and assists, keep those. But 2 of the same is annoying".
    fo_ok, fo_out = _mx_fold_check(js)
    ck("Matchups tab (one line per player and stat, the owner 2026-10-30): by default the scanner shows each player's "
       "best rung of each stat, YES and NO alike -- the one the chosen sort ranks highest (Best picks: the +4c floor "
       "over the +9c line whose floor is -1c and the NO +5c whose floor is +3c; Highest EV: that +9c line; Highest "
       "probability: the 10+ NO at 90%; Biggest edge: a tie broken to the 6+), still the highest when the column is turned ascending, Best picks breaking a "
       "tie (Most evidence ties a ladder) -- with '+N more' on its row and the count above the table; a different stat "
       "(points beside assists) and a different player keep their rows, two players whose id came through as 'None' "
       "are never merged, a chip or a stat filter folds what it keeps (the strong-fit chip shows the 8+ that carries "
       "it), the box unticked shows every rung, and the choice is stored and refuses a non-boolean",
       fo_ok is not False, str((fo_ok, fo_out)))
    css = open(os.path.join(_ROOT, "static", "style.css")).read()
    root_css = re.search(r":root\s*\{([^}]*)\}", css)
    tok = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", root_css.group(1))) if root_css else {}

    def _lum(h):
        def lin(c):
            c = c / 255.0
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        r_, g_, b_ = (int(h[i:i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * lin(r_) + 0.7152 * lin(g_) + 0.0722 * lin(b_)

    def _contrast(a, b):
        hi_, lo_ = sorted((_lum(a), _lum(b)), reverse=True)
        return (hi_ + 0.05) / (lo_ + 0.05)
    surfaces = [tok.get(k) for k in ("bg", "bg2", "card", "card2")]
    a_rule = re.search(r"(?m)^a\s*\{([^}]*)\}", css)
    hover_rule = re.search(r"(?m)^a:where\(:hover\)\s*\{([^}]*)\}", css)
    ratios = [round(_contrast(tok[c], s), 2) for c in ("link", "link-hover") for s in surfaces] \
        if tok.get("link") and tok.get("link-hover") and all(surfaces) else []
    ck("links are readable on every surface: one app-wide colour (--link, and --link-hover) at 7:1 or better on the "
       "page, the bars and both card tints -- the browser's own #0000EE was 1.9:1 on a card, 1.6:1 visited, the "
       "owner's 'this black on blue is hard to read' on the Matchups scanner's player names (2026-10-30); that link "
       "carries no colour of its own, so the rule reaches it, and the hover rule sits inside :where() so it weighs no "
       "more than `a` (a bare a:hover outweighs a class, and the comment promises a class still wins)",
       bool(a_rule) and "var(--link)" in a_rule.group(1) and bool(hover_rule) and "var(--link-hover)" in hover_rule.group(1)
       and not re.search(r"(?m)^a:hover\b", css)
       and len(ratios) == 8 and min(ratios) >= 7.0
       and re.search(r'<a href="#" data-mx="open" data-val="\$\{_mxEsc\(r\.p\.player_id\)\}">', js) is not None,
       str((ratios, a_rule.group(0) if a_rule else None)))
    labs = re.findall(r'<label style="(display:inline-flex[^"]*)"><input type="checkbox" data-mx="(\w+)"', js)
    ck("Matchups tab: the scanner's two checkboxes sit BESIDE their text -- style.css's global label rule is a column "
       "(label { display: flex; flex-direction: column }), and an inline display alone left each box above its words",
       sorted(m for _st, m in labs) == ["fold", "listed"] and all("flex-direction:row" in st for st, _m in labs)
       and re.search(r"(?m)^label \{[^}]*flex-direction: column", css) is not None, str(labs))
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
    # the board-only Kalshi series (the rushing markets), kept out of the shared index
    import kalshi_nfl as _KN

    def _mk(series, ev, name, strike, ya, na):
        return {"ticker": f"{series}-{ev}-X-{int(strike + 0.5)}", "event_ticker": f"{series}-{ev}",
                "title": f"{name}: {int(strike + 0.5)}+ thing", "floor_strike": strike,
                "yes_ask_dollars": f"{ya / 100:.2f}", "no_ask_dollars": f"{na / 100:.2f}",
                "yes_bid_dollars": f"{(ya - 2) / 100:.2f}", "no_bid_dollars": f"{(na - 2) / 100:.2f}",
                "close_time": "2026-11-08T18:00:00Z"}
    feed = {"KXNFLRSHATT": [_mk("KXNFLRSHATT", "26NOV08AB", "Joe Back", 17.5, 40, 62)],
            "KXNFLRRYDS": [_mk("KXNFLRRYDS", "26NOV08AB", "Joe Back", 89.5, 45, 57),
                           _mk("KXNFLRRYDS", "26NOV08AB", "ATL Falcons D/ST", 0.5, 50, 52)],
            "KXNFLLONGRSH": RuntimeError("page 2 failed")}
    saved_fetch, saved_cache = _KN._fetch, dict(_KN._extra_cache)
    pulled = []

    def fake_fetch(series, max_pages=None):
        pulled.append(series)
        v = feed[series]
        if isinstance(v, Exception):
            raise v
        return v
    _KN._fetch = fake_fetch
    _KN._extra_cache.update(ts=0.0, data=None)
    try:
        ex = _KN.extra_props()
        ex2 = _KN.extra_props()
    finally:
        _KN._fetch = saved_fetch
        _KN._extra_cache.clear()
        _KN._extra_cache.update(saved_cache)
    e = ex.get("26NOV08AB") or {}
    import nfl_matchup as _NM
    kl = _NM._kalshi_lines({}, "26NOV08AB", ts=5.0, extra=ex)
    ck("the board's extra Kalshi series (rush attempts, rush + rec yards, longest rush) parse exactly as the shared "
       "index's props (one parser), D/ST team markets dropped; a series whose pages fail is left out WHOLE and "
       "ledgered; the read is cached; they reach the board's lines through _kalshi_lines(extra=) and never the "
       "shared index the combo maker and the props recorder read",
       set(e.get("players") or {}) == {("rush_att", "joe back", 17.5), ("rush_rec_yd", "joe back", 89.5)}
       and kl.get(("rush_att", "joe back"), {}).get(18, {}).get("yes_ask") == 40
       and kl.get(("rush_rec_yd", "joe back"), {}).get(90, {}).get("no_ask") == 57
       and ex2 is ex and pulled.count("KXNFLRSHATT") == 1 and _ledger_has_any("KNFL-extra") is not False
       and set(_KN.EXTRA_PROP_SERIES) & set(_KN._PROP_SERIES) == set(),
       str((e.get("players"), kl, pulled)))
    import nfl_rush_data as _RD
    ck("the job's completeness test is the rushing layer's own, and strict: an aux saved before the 'complete' flag "
       "existed, or none at all, is incomplete (the data confirmation's G1b)",
       _RD.aux_complete({"schema": 2}) is False and _RD.aux_complete(None) is False
       and _RD.aux_complete({"schema": 2, "complete": True}) is True
       and "R.aux_complete(aux)" in inspect.getsource(matchups.nfl_data_job))
    ck("the rushing layer's aux store rides the artifact contract: SCHEMA 7 names nfl_rush_aux_<season>.pkl, which the "
       "nfl_matchup job builds beside the matchup store (pc_worker's job calls the same function, and its sync uploads "
       "any deep-store file fresher than the server's)",
       artifacts.SCHEMA >= 7 and "nfl_rush_aux_<season>.pkl" in art and "R.refresh(seasons=[s]" in inspect.getsource(
           matchups.nfl_data_job))
    json.dumps(idx)            # the index is plain JSON

    # the live view (the owner, 2026-10-24): a player leaves the list once his game starts; the next day's
    # board takes over at midnight MT and is never mixed into the pool of unplayed players. The fixture's
    # clock is 18:00 MT on 2026-11-05.
    now = 1793926800.0
    H = 3600.0

    def _g(gid, start, state=None):
        return {"game_id": gid, "home": "H" + gid, "away": "A" + gid, "start_ts": start, "state": state}

    def _p(pid, gid):
        return {"player_id": pid, "name": "P" + pid, "team": "T", "opp": "O", "game_id": gid, "markets": []}
    today_idx = {"sport": "nba", "built_ts": now - 600, "notes": ["today note"], "games": [
        _g("1", now + H), _g("2", now - H), _g("3", now - 4 * H), _g("4", None), _g("5", None, "in"), _g("6", now)],
        "players": [_p("a", "1"), _p("b", "2"), _p("c", "3"), _p("f", "4"), _p("g", "5"), _p("h", "6")]}
    lv, gone = matchups.drop_started(today_idx, now=now)
    same, gone0 = matchups.drop_started(dict(today_idx, games=[_g("1", now + H)], players=[_p("a", "1")]), now=now)
    ck("matchups live view: a player leaves the list the moment his game starts (b under way, c over, h tipping "
       "now); a game ahead stays (a); a game with no start time stays (f) unless its board says it is in play (g); "
       "the published board is never changed; the slate says why and that the next day's board shows from "
       "midnight MT, the epoch of which rides the payload",
       [p["player_id"] for p in lv["players"]] == ["a", "f"] and gone == {"2", "3", "5", "6"}
       and [g["game_id"] for g in lv["games"]] == ["1", "4"] and len(today_idx["players"]) == 6
       and any("under way or over" in n and "midnight" in n for n in lv["notes"]) and lv.get("live_filtered")
       and lv.get("next_day_ts") == 1793948400.0 and gone0 == set() and same["notes"] == ["today note"],
       str(([p["player_id"] for p in lv["players"]], gone, lv.get("next_day_ts"), lv["notes"])))
    saved_board, saved_today = matchups.board, matchups.today_mt
    asked = []
    import app as A2
    saved_ds, saved_sp, saved_bld, saved_game = matchups.drop_started, matchups.started_piece, matchups.buildable, \
        matchups.game
    matchups.board = lambda sport, key=None: (asked.append(key) or (today_idx, 60.0, False))
    matchups.today_mt = lambda: "2026-11-05"
    matchups.drop_started = lambda idx, now=None: saved_ds(idx, now=1793926800.0)
    matchups.started_piece = lambda piece, now=None: saved_sp(piece, now=1793926800.0)
    matchups.buildable = lambda key, today=None: key in ("2026-11-05", "2026-11-06")
    # a real piece carries the whole slate's games list: it must be judged by ITS game (the re-check's V12)
    matchups.game = lambda sport, key, gid: ({"game_id": gid, "games": list(today_idx["games"]),
                                              "players": [_p("b", gid)]}, 10.0)
    try:
        cl2 = A2.app.test_client()
        r_def = cl2.get("/api/matchups?sport=nba").get_json()
        r_day = cl2.get("/api/matchups?sport=nba&date=2026-11-05").get_json()
        r_past = cl2.get("/api/matchups?sport=nba&date=2026-10-01").get_json()
        r_live_piece = cl2.get("/api/matchups?sport=nba&date=2026-11-05&game=2").get_json()
        r_pre_piece = cl2.get("/api/matchups?sport=nba&date=2026-11-05&game=1").get_json()
        r_past_piece = cl2.get("/api/matchups?sport=nba&date=2026-10-01&game=2").get_json()
    finally:
        matchups.board, matchups.today_mt = saved_board, saved_today
        matchups.drop_started, matchups.started_piece, matchups.buildable = saved_ds, saved_sp, saved_bld
        matchups.game = saved_game
    ck("matchups live view: /api/matchups serves today's board (no next day mixed in: only today's board is ever "
       "asked for) less its started games, as does an explicit today; a past date serves its last publish as it is; "
       "and says its quotes are not a pre-game record; a started game's DETAIL piece is refused on a buildable date "
       "(it would carry in-game quotes), judged by its own game in the slate's list, while a game ahead, or a past "
       "date's, is served",
       {p["player_id"] for p in r_def["players"]} == {"a", "f"} and {p["player_id"] for p in r_day["players"]} == {"a", "f"}
       and len(r_past["players"]) == 6 and set(asked) == {"2026-11-05", "2026-10-01"}
       and any("last publish" in n for n in r_past["notes"])
       and r_live_piece.get("status") == "none" and "under way" in (r_live_piece.get("why") or "")
       and r_pre_piece.get("status") == "ok" and r_past_piece.get("status") == "ok",
       str((r_def.get("players"), asked, r_live_piece)))
    js_live = open(os.path.join(_ROOT, "static", "matchups.js")).read()
    lv_ok, lv_out = None, ""
    try:
        import shutil
        import subprocess
        if shutil.which("node"):
            h4 = ("const _els = {}; const $ = (id) => (_els[id] = _els[id] || {innerHTML: '', value: '', dataset: {}});\n"
                  "globalThis.document = {visibilityState: 'visible', querySelectorAll: () => [], addEventListener: () => {}};\n"
                  "let waited = null, cb = null; globalThis.setTimeout = (f, ms) => { waited = ms; cb = f; return 1; };\n"
                  "globalThis.clearTimeout = () => {};\n" + js_live + "\n"
                  "(async () => {\n"
                  "const nowS = Date.now() / 1000;\n"
                  "const L = [{k: 10, p: 0.5, labels: [], market: {yes_ask: 50, no_ask: 52}}];\n"
                  "const d = {status: 'ok', date: '2026-11-05', live_filtered: true, next_day_ts: nowS + 7200, games: ["
                  "{game_id: '1', away: 'A1', home: 'H1', start_ts: nowS - 60},"
                  "{game_id: '2', away: 'A2', home: 'H2', start_ts: nowS + 3600}],"
                  " players: [{player_id: 'x', name: 'Xx', team: 'T', opp: 'O', game_id: '1', markets: [{stat: 'pts', lines: L}]},"
                  "{player_id: 'y', name: 'Yy', team: 'T', opp: 'O', game_id: '2', markets: [{stat: 'pts', lines: L}]}]};\n"
                  "let served = d, fetches = 0;\n"
                  "globalThis.fetch = async () => { fetches += 1; return {status: 200, json: async () => served}; };\n"
                  "_mx.listed = false; _mx.data = d; const html = mxScanView(d);\n"
                  "await mxLoad(); const armed = waited;\n"
                  "const done = Object.assign({}, d, {players: [d.players[0]]}); _mx.data = done; _mxLiveTimer(); const wDone = waited;\n"
                  "_mx.data = Object.assign({}, done, {placeholder: true}); _mxLiveTimer(); const wPh = waited;\n"
                  "_mx.data = Object.assign({}, d, {games: [d.games[0], Object.assign({}, d.games[1], {start_ts: nowS + 300})]});"
                  " _mxLiveTimer(); const wSoon = waited;\n"
                  "_mx.date = '2026-11-05'; _mx.data = d; _mxLiveTimer(); served = d; await cb(); const keptPick = _mx.date;\n"
                  "_mx.data = d; _mx.tries = 8; _mx.date = '2026-11-05';\n"
                  "_mx.data = Object.assign({}, d, {next_day_ts: nowS - 5}); _mxLiveTimer();\n"
                  "served = Object.assign({}, d); const f0 = fetches; await cb();\n"
                  "await new Promise((r) => setImmediate(r));\n"
                  "globalThis.fetch = async () => { throw new Error('down'); }; waited = null; _mx.retry = null;\n"
                  "await mxLoad(true); const wErr = waited;\n"
                  "globalThis.fetch = async () => ({status: 200, json: async () => ({status: 'none', why: 'no board'})});"
                  " waited = null; _mx.retry = null; await mxLoad(true); const wNone = waited;\n"
                  "_mx.tries = 7; served = d; globalThis.fetch = async () => ({status: 200, json: async () => served});"
                  " const fN = cb; fN(); const triesAfterRetry = _mx.tries;\n"
                  "process.stdout.write(JSON.stringify({started: [..._mxStarted(d)], x: html.includes('Xx'), y: html.includes('Yy'),"
                  " a1: html.includes('A1 @ H1'), a2: html.includes('A2 @ H2'), armed, wDone, tries: _mx.tries,"
                  " date: _mx.date, refetched: fetches > f0, stopped: !!(_mx.data || {}).stopped, wErr, wPh, wSoon, keptPick,"
                  " wNone, triesAfterRetry}));\n"
                  "})();")
            r4 = subprocess.run(["node", "-e", h4], capture_output=True, text=True, timeout=30)
            o4 = json.loads(r4.stdout) if r4.returncode == 0 and r4.stdout else None
            lv_out = r4.stdout[:600] + r4.stderr[-400:]
            lv_ok = bool(o4) and o4["started"] == ["1"] and not o4["x"] and o4["y"] and not o4["a1"] and o4["a2"] \
                and o4["armed"] == 900000 and 7200000 <= o4["wDone"] <= 7206000 and o4["tries"] == 0 \
                and o4["date"] == "" and o4["refetched"] and not o4["stopped"] and o4["wErr"] == 120000 \
                and o4["wPh"] == 900000 and 300000 <= o4["wSoon"] <= 306000 and o4["keptPick"] == "2026-11-05" \
                and o4["wNone"] == 120000 and o4["triesAfterRetry"] == 0
    except Exception as e:
        lv_ok, lv_out = False, repr(e)
    ck("Matchups tab live view (in the browser): a game that starts while the tab is open drops its players and leaves "
       "the game picker on the page's own clock; a load arms the live timer -- at the next start and at least every "
       "fifteen minutes (the boards' TTL) while anyone is left, else only for midnight MT (no rebuilds of an empty "
       "list); a failed reload looks again in two minutes instead of ending the polling; at "
       "midnight a date picked as today follows the day and the poll's building retries start afresh (the re-check's "
       "R-1/R-3: it stalled on 'still building' or served the finished board); a tab back from the background "
       "re-renders; a game filter whose game left is cleared",
       lv_ok is not False and '_mx.game = ""' in js_live and '"visibilitychange"' in js_live, str((lv_ok, lv_out)))

    # the NFL data job backfills the two seasons the profiles read back, once
    import clock
    daily = [2026] if clock.today_et().month >= 10 else [2025, 2026]
    calls = []
    fake = _fake_d(calls, load_season=lambda s: {"schema": 2, "built": 1.0, "n": 5} if s == 2025 else None)
    aux_ok = lambda s: {"store_built": 1.0, "store_n": 5, "complete": True}  # noqa: E731 -- matches the fake store
    _run_job(fake, _fake_r(calls, aux=aux_ok))
    first = [c for c in calls if c[0] == "D"]
    fake.load_season = lambda s: {"schema": 2, "built": 1.0, "n": 5}
    calls.clear()
    _run_job(fake, _fake_r(calls, aux=aux_ok))
    second = [c for c in calls if c[0] == "D"]
    ck("nfl_matchup job: a season the profiles read back (two before the current) with no store is backfilled once "
       "(its raw files kept until the rushing layer has read them, then removed); the daily refresh follows; with both "
       "stores present and matching only the daily runs",
       first == [("D", [2024], True), ("D", daily, True)] and second == [("D", daily, True)], str((first, second)))

    # the rushing layer's aux is backfilled on ITS OWN status (review O1 / Opus M2 / Sonnet M1): an instance whose
    # matchup backfill already ran (both stores present) must still build 2024-25's aux, from one fresh download
    # the rushing checks run in every month (the data re-check's R5: gated on October they silently skipped
    # January to September): the job reads the clock only for the daily rule's month
    import clock as _clock
    _saved_et = _clock.today_et
    _clock.today_et = lambda: __import__("datetime").date(2026, 10, 24)
    try:
        rc = []
        store = lambda s: {"schema": 2, "built": 7.0, "n": 9}            # noqa: E731
        aux_ok_s = lambda s: {"store_built": 7.0, "store_n": 9, "complete": True}  # noqa: E731
        fd = _fake_d(rc, load_season=store)
        stale = {2024: None, 2025: {"store_built": 3.0, "store_n": 9}}  # missing, and built on another store
        res, left, pre = _run_job(fd, _fake_r(rc, aux=lambda s: stale.get(s, {"store_built": 7.0, "store_n": 9,
                                                                             "complete": True})),
                                  plant=[2024, 2025])
        ck("rushing aux (O1): with both matchup stores present, a missing or stale prior-season aux backfills the "
           "season -- stale raw files removed FIRST (so the matchup store is rebuilt from the same download the aux "
           "reads), one season at a time (no other prior season's files on disk while one downloads: ~77 MB, not "
           "~154), the matchup store refreshed with its files kept, its aux built, its files dropped; the daily season "
           "follows on both layers",
           rc == [("D", [2024], True), ("R", [2024], False), ("D", [2025], True), ("R", [2025], False),
                  ("D", [2026], True), ("R", [2026], True)] and pre == [[], []] and left == [], str((rc, pre, left)))
        rc2 = []
        boom = _fake_r(rc2, aux=lambda s: None, fail={2024})
        res2, left2, _pre2 = _run_job(_fake_d(rc2, load_season=store), boom, plant=[2024, 2025])
        ck("rushing aux (O1): one season's aux failure never costs another's, nor the matchup data's; it is ledgered "
           "RUSH-refresh, reported in the job's result, and the prior seasons' raw files are still removed",
           ("R", [2025], False) in rc2 and ("R", [2026], True) in rc2 and "error" in (res2.get("rush") or {}).get(2024, {})
           and res2["rush"][2025] == {"ok": 2025} and left2 == [] and _ledger_has("RUSH-refresh"), str((rc2, res2, left2)))
        rc3 = []
        fd3 = _fake_d(rc3, load_season=store, daily_raises=True)
        res3, left3, _pre3 = _run_job(fd3, _fake_r(rc3, aux=lambda s: None), plant=[2024, 2025])
        ck("rushing aux (O1): a daily refresh that raises still leaves no prior-season raw file behind (the cleanup is a "
           "finally: a stranded backfill is ~154 MB on a 1 GB disk), and the error still reaches the job",
           isinstance(res3, RuntimeError) and left3 == [], str((res3, left3)))
        rc5 = []
        res5, left5, _pre5 = _run_job(_fake_d(rc5, load_season=lambda s: None if s == 2025 else store(s),
                                              build_raises={2025}),
                                      _fake_r(rc5, aux=lambda s: None), plant=[])
        ck("rushing aux (O1): the matchup data's OWN backfill (no store) that fails after its download leaves no file "
           "behind (the finally covers the season in flight), and the error reaches the job, as it always did",
           isinstance(res5, RuntimeError) and left5 == [], str((rc5, res5, left5)))
        rc6 = []
        res6, left6, _pre6 = _run_job(_fake_d(rc6, load_season=store, build_raises={2025}),
                                      _fake_r(rc6, aux=lambda s: None), plant=[])
        ck("rushing aux (re-check R2): a rebuild of a HEALTHY matchup store that only the rushing layer asked for, when "
           "it fails, is ledgered NFLM-backfill and never costs the daily refresh; its files still go",
           isinstance(res6, dict) and "error" in res6["backfill"][2025] and ("D", [2026], True) in rc6
           and ("R", [2026], True) in rc6 and ("R", [2025], False) not in rc6 and left6 == []
           and _ledger_has("NFLM-backfill"), str((rc6, res6, left6)))
        rc7 = []
        res7, _l7, _p7 = _run_job(_fake_d(rc7, load_season=store), _fake_r(rc7, aux=lambda s: None),
                                  plant=[], marks={2024: 0.5, 2025: 8.0})
        ck("rushing aux (re-check R3): a rushing-only backfill is tried at most once a week -- a season tried half a "
           "day ago waits, one tried eight days ago goes again -- so a persistent failure is not a daily 77 MB "
           "download and rebuild",
           [c for c in rc7 if c[1] != [2026]] == [("D", [2025], True), ("R", [2025], False)], str(rc7))
        rc8 = []
        _run_job(_fake_d(rc8, load_season=store),
                 _fake_r(rc8, aux=lambda s: {"store_built": 7.0, "store_n": 9, "complete": s != 2025}), plant=[])
        ck("rushing aux (re-checks Sonnet R1 / Opus R3): an aux that matches its store but was saved WITHOUT a source "
           "its season should have (a failed download) is due again -- it was 'aligned' forever before",
           [c for c in rc8 if c[1] != [2026]] == [("D", [2025], True), ("R", [2025], False)], str(rc8))
        rc9, d9 = [], tempfile.mkdtemp(prefix="mx_job_keep_")
        _run_job(_fake_d(rc9, load_season=store), _fake_r(rc9, aux=lambda s: None, fail={2024, 2025}), plant=[],
                 store_dir=d9)
        first9 = [c for c in rc9 if c[1] != [2026]]
        rc9.clear()
        _run_job(_fake_d(rc9, load_season=store), _fake_r(rc9, aux=lambda s: None, fail={2024, 2025}), plant=[],
                 store_dir=d9)
        ck("rushing aux (re-check R6): the weekly marker is WRITTEN by a failed rushing-only backfill -- the next day's "
           "run, with nothing planted, skips both seasons (a marker never saved, or stamped 0, made the throttle inert: "
           "the daily 77 MB download R3 was about)",
           first9 == [("D", [2024], True), ("R", [2024], False), ("D", [2025], True), ("R", [2025], False)]
           and [c for c in rc9 if c[1] != [2026]] == [], str((first9, rc9)))
        rc10 = []
        res10, _l10, _p10 = _run_job(_fake_d(rc10, load_season=store), _fake_r(rc10, aux=aux_ok_s), plant=[])
        n_vec = len(_LEDGER.get("vectors") or [])
        _LEDGER["vectors_raise"] = True
        try:
            res11, _l11, _p11 = _run_job(_fake_d([], load_season=store), _fake_r([], aux=aux_ok_s), plant=[])
        finally:
            _LEDGER["vectors_raise"] = False
        ck("nfl_matchup job: the rushing model's comparables history is precomputed in the job (once, after the daily "
           "rushing refresh) so a board never pays its first build; its failure is ledgered NFLR-vectors and never "
           "costs the matchup data",
           n_vec == 1 and isinstance(res10, dict) and res10.get("rush_vectors") == {"vectors": 0}
           and isinstance(res11, dict) and "rush_vectors" not in res11 and _ledger_has("NFLR-vectors"),
           str((n_vec, res10 if isinstance(res10, Exception) else res10.get("rush_vectors"), res11)))
        rc4 = []
        res4, _l4, _p4 = _run_job(_fake_d(rc4, load_season=store), "broken", plant=[])
        ck("rushing aux (O1): a rushing layer that cannot be imported never stops the matchup data's own refresh",
           [c for c in rc4 if c[0] == "D"] == [("D", [2026], True)] and res4.get("rush") is None, str((rc4, res4)))
    finally:
        _clock.today_et = _saved_et
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
