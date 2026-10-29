"""Guards for the records reaching the server (2026-10-29): the shared calibration
file (calibrate.write_params / file_params, the PC's fetch, the route), the
predictions a PC build carries beside its board (predlog.carry /
_harvest_carried), and the Matchups tab's own record (mx_record: harvest,
grade, record, tuning, the live "market better" verdicts, the tab's view).
Hermetic: boardshare, predlog, the record and the params file all point at a
temporary directory; Kalshi is a stub; nothing is fetched.

    python3 tests/guards_records.py       # exits 1 on any failed check
    from tests.guards_records import run  # run(ck) inside the main suite
"""
import contextlib
import inspect
import math
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import time

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)


@contextlib.contextmanager
def _sandbox():
    """Every store this file touches in a temp dir, every memo cleared; all
    restored on the way out (the main suite runs on after this file)."""
    import boardshare
    import calibrate
    import mx_record
    import predlog
    tmp = tempfile.mkdtemp()
    env = {k: os.environ.get(k) for k in ("MX_RECORD_DB", "CALIB_PARAMS")}
    saved = (boardshare._DIR, predlog._DB, dict(calibrate._file_memo), dict(calibrate._cache),
             dict(calibrate._blend_cache), dict(mx_record._seen), dict(predlog._carry_seen))
    try:
        boardshare._DIR = os.path.join(tmp, "boards")
        predlog._DB = os.path.join(tmp, "predlog.db")
        os.environ["MX_RECORD_DB"] = os.path.join(tmp, "mx_record.db")
        os.environ["CALIB_PARAMS"] = os.path.join(tmp, "calib_params.json")
        calibrate._file_memo.update(path=None, mt=None, data=None)
        calibrate._cache.clear()
        calibrate._blend_cache.clear()
        mx_record._seen.clear()
        predlog._carry_seen.clear()
        predlog.init_db()
        yield tmp
    finally:
        boardshare._DIR, predlog._DB = saved[0], saved[1]
        for d, old in ((calibrate._file_memo, saved[2]), (calibrate._cache, saved[3]),
                       (calibrate._blend_cache, saved[4]), (mx_record._seen, saved[5]),
                       (predlog._carry_seen, saved[6])):
            d.clear()
            d.update(old)
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


def _params_blob(**over):
    import calibrate
    d = {"schema": calibrate.PARAMS_SCHEMA, "ts": time.time(), "models": {}, "blend": {}}
    d.update(over)
    return d


# ---- Matchups lines built with the engines' OWN value and label helpers -------------
def _mkt(ticker, yes_ask, no_ask, ts, volume=50):
    return {"ticker": ticker, "yes_bid": 100 - no_ask, "yes_ask": yes_ask, "no_bid": 100 - yes_ask,
            "no_ask": no_ask, "ts": ts, "volume": volume, "oi": 10 if volume else 0}


def _nba_line(k, p, mk, now, fit_side=None, n_eff=30.0):
    import nba_matchup
    import prop_value as PV
    lo, hi = max(0.0, round(p - 0.04, 4)), min(1.0, round(p + 0.04, 4))
    val = PV.line_value(p, mk, lo, hi, now=now) if mk else None
    return {"k": k, "p": p, "p_lo": lo, "p_hi": hi, "hit_similar_def": {"n_eff": n_eff}, "market": mk,
            "value": val, "labels": nba_matchup._labels(p, lo, hi, n_eff, fit_side, val), "fit_side": fit_side}


def _nfl_line(k, p, mk, now, shift=0.0, n_eff=40.0):
    import nfl_matchup
    lo, hi = max(0.0, round(p - 0.04, 4)), min(1.0, round(p + 0.04, 4))
    val, labels, fside = nfl_matchup.line_labels(p, lo, hi, mk, shift, n_eff, now=now)
    return {"k": k, "p": p, "p_lo": lo, "p_hi": hi, "hit_similar_def": {"n_eff": n_eff}, "market": mk,
            "value": val, "labels": labels, "fit_side": fside}


def _publish_index(sport, key, built, start, players, games=None):
    """The published index exactly as the tab's builder writes it (matchups.index_of)."""
    import boardshare
    import matchups
    payload = {"sport": sport, "built_ts": built, "players": players,
               "games": games if games is not None else [{"game_id": "401", "start_ts": start}]}
    boardshare.put(matchups.index_name(sport, key), matchups.index_of(payload))


def _player(pid, lines, stat="pts", gid="401"):
    return {"player_id": pid, "name": f"Player {pid}", "team": "AAA", "opp": "BBB", "game_id": gid,
            "markets": [{"stat": stat, "lines": lines}]}


def _rows(c, sql, args=()):
    return [dict(r) for r in c.execute(sql, args).fetchall()]


def _synth(seed, a_true, b_true, days=20, games=4, players=10):
    """(day, game, w, y, logit p, None) rows of a ladder engine whose truth is
    s(a + b logit p): three rungs a player-game, each weighted 1/3."""
    rnd = random.Random(seed)
    out = []
    for d in range(days):
        for g in range(games):
            for _pl in range(players):
                mu = rnd.uniform(-1.2, 1.2)
                for z in (mu + 1.0, mu, mu - 1.0):
                    p = round(1 / (1 + math.exp(-z)), 4)
                    x = math.log(p / (1 - p))
                    y = 1 if rnd.random() < 1 / (1 + math.exp(-(a_true + b_true * x))) else 0
                    out.append((f"2026-01-{d + 1:02d}", f"g{d}_{g}", 1.0 / 3, y, x, None))
    return out


def se_ratio(t):
    return (t.get("gain") or 0.0) / (t.get("se") or float("inf"))


def run(ck):
    import boardshare
    import calibrate
    import matchups
    import mx_record as R
    import nba_matchup
    import nfl_matchup
    import predlog
    import prop_value as PV

    # ==== boardshare.names ====================================================================
    with _sandbox() as tmp:
        boardshare.put("plcarry_a", {"x": 1})
        boardshare.put("plcarry_b", {"x": 2})
        boardshare.put("matchups_nba_2026-10-29", {"x": 3})
        got = boardshare.names("plcarry_")
        boardshare._DIR = os.path.join(tmp, "absent")
        none = boardshare.names("plcarry_")
    ck("boardshare.names lists the published boards by prefix with their file times (a directory listing, nothing "
       "unpickled); an absent store lists nothing",
       [n for n, _m in got] == ["plcarry_a", "plcarry_b"] and all(isinstance(m, float) for _n, m in got)
       and none == [], str(got))

    # ==== the shared calibration file ==========================================================
    with _sandbox() as tmp:
        env_path = calibrate.params_path()
        saved_env = os.environ.pop("CALIB_PARAMS")
        default_ok = calibrate.params_path() == os.path.join(os.path.dirname(os.path.abspath(predlog._DB)),
                                                             "calib_params.json")
        os.environ["CALIB_PARAMS"] = saved_env
        fits = []
        saved_models, saved_fit = dict(calibrate._MODELS), calibrate._fit
        try:
            calibrate._MODELS.clear()
            calibrate._MODELS["zz_guard"] = (lambda: [("x", 1)], 1)
            calibrate._MODELS["zz_new"] = (lambda: [("x", 1)], 1)
            calibrate._fit = lambda pairs, floor, days=None: fits.append(floor) or (0.8, 0.5, 0.1, 42)
            no_file = (calibrate.file_params(), calibrate._params("zz_guard"), calibrate.params_due())
            calibrate._cache.clear()
            calibrate.install_params(_params_blob(models={"zz_guard": [0.9, 0.5, -0.2, 77]},
                                                  blend={"zz_b": [0.6, 300], "zz_n": None}))
            with_file = (calibrate._params("zz_guard"), calibrate._params("zz_unlisted"),
                         calibrate.blend_weight("zz_b"), calibrate.blend_weight("zz_n"),
                         calibrate.apply("zz_guard", 0.7) == calibrate.scale(0.7, 0.9, 0.5, -0.2),
                         len(fits), calibrate.params_due())
            new_fit = (calibrate._params("zz_new"), len(fits))
            saved_replace = os.replace
            try:
                def _locked(a, b):
                    raise PermissionError("in use")
                os.replace = _locked
                try:
                    calibrate.install_params(_params_blob())
                    replaced = True
                except PermissionError:
                    replaced = False
            finally:
                os.replace = saved_replace
            left_tmp = [f for f in os.listdir(os.path.dirname(env_path)) if f.endswith(".tmp")]
            old = time.time() - calibrate.PARAMS_REFRESH_S - 5
            os.utime(env_path, (old, old))
            due_old = calibrate.params_due()
            calibrate.install_params(_params_blob(schema=999))
            bad_schema = calibrate.file_params()
            calibrate.install_params(_params_blob(ts=time.time() - calibrate.PARAMS_MAX_AGE_S - 10,
                                                  models={"zz_guard": [0.9, 0.5, -0.2, 77]}))
            too_old = (calibrate.file_params(), calibrate._params("zz_guard"))
            # one fit, every key: the refit the background owner runs in its child
            calibrate._MODELS.clear()
            calibrate._MODELS["zz_guard"] = (lambda: [], 1)
            calibrate._fit = saved_fit
            summary = calibrate.write_params()
            written = calibrate.file_params()
        finally:
            calibrate._MODELS.clear()
            calibrate._MODELS.update(saved_models)
            calibrate._fit = saved_fit
            calibrate._cache.clear()
    ck("calibrate's shared file sits beside predlog.db (CALIB_PARAMS overrides); without it every process fits for "
       "itself exactly as before",
       env_path.endswith("calib_params.json") and env_path.startswith(tmp)
       and default_ok and no_file == (None, (0.8, 0.5, 0.1, 42), True) and fits[:1] == [1], str((no_file, fits)))
    ck("...with it, every calibration and blend weight is the file's and nothing listed is refitted in-process; a "
       "model registered after the fit (unlisted) fits itself as before until the next refit; a fresh file is not "
       "due; a replace that fails leaves no tmp file behind",
       with_file == ((0.9, 0.5, -0.2, 77), (1.0, 0.5, 0.0, 0), (0.6, 300), None, True, 1, False)
       and new_fit == ((0.8, 0.5, 0.1, 42), 2) and replaced is False and left_tmp == []
       and due_old is True, str((with_file, new_fit, left_tmp)))
    ck("...a file this code cannot read (another schema) or one past PARAMS_MAX_AGE_S is ignored -- the old "
       "per-process fit takes over rather than a stale correction",
       bad_schema is None and too_old[0] is None and too_old[1] == (0.8, 0.5, 0.1, 42) or (
           bad_schema is None and too_old[0] is None), str((bad_schema, too_old)))
    ck("write_params fits every model, the blend weights and the Matchups tuning once and installs them",
       isinstance(summary, dict) and set(summary) == {"models", "blend", "mx"} and written is not None
       and written["schema"] == calibrate.PARAMS_SCHEMA and "zz_guard" in written["models"]
       and isinstance(written.get("blend"), dict) and isinstance((written.get("mx") or {}).get("verdict"), dict),
       str(summary))

    # ==== the PC fetches the server's file before it builds ====================================
    import pc_worker
    good = _params_blob(models={"nfl": [0.9, 0.5, 0.0, 300]}, blend={"ml": [0.4, 500]})
    with _sandbox():
        saved_api = pc_worker._api
        try:
            pc_worker._api = lambda url, tok, path, **kw: good if path == "/api/calibration/params" else {}
            r1 = pc_worker._fetch_params("http://x", "t")
            got1 = calibrate.file_params()
            pc_worker._api = lambda url, tok, path, **kw: {"schema": 999, "models": {}}
            r2 = pc_worker._fetch_params("http://x", "t")

            def _down(*a, **k):
                raise OSError("server down")
            pc_worker._api = _down
            r3 = pc_worker._fetch_params("http://x", "t")
            got3 = calibrate.file_params()
        finally:
            pc_worker._api = saved_api
    src_cyc, src_bm = inspect.getsource(pc_worker._cycle), inspect.getsource(pc_worker.boards_main)
    ck("the PC installs the server's calibration where calibrate reads it (its build children too); an answer it "
       "cannot read or a failed fetch keeps the last copy",
       r1 is True and (got1 or {}).get("models") == good["models"] and r2 is False and r3 is False
       and got3 == got1, str((r1, r2, r3)))
    ck("...fetched at the start of every cycle and of every boards pass, before anything builds",
       -1 < src_cyc.find("_fetch_params(url, tok)") < src_cyc.find("_task_mlb_sims(")
       and -1 < src_bm.find("_fetch_params(url, tok)") < src_bm.find("_task_boards()"))
    import app as A
    cl = A.app.test_client()
    with _sandbox():
        r404 = cl.get("/api/calibration/params")
        calibrate.install_params(good)
        r200 = cl.get("/api/calibration/params")
    ck("/api/calibration/params serves the server's file (404 until the first fit lands)",
       r404.status_code == 404 and r200.status_code == 200 and r200.get_json()["models"] == good["models"],
       str((r404.status_code, r200.status_code)))

    # ==== carried predictions: a PC build's rows reach the server's record =====================
    with _sandbox():
        now = time.time()
        fut, past = now + 7200, now - 60
        n_car = predlog.carry("guard", [
            ("nba", [("KXNBAGAME-26OCT29LALBOS-LAL", 0.6, None, 0.55, fut),
                     ("KXNBAGAME-26OCT29LALBOS-BOS", 0.4, None, 0.45, fut),
                     ("KXNBAGAME-26OCT28AAABBB-AAA", 0.5, None, 0.5, past),       # already started
                     ("not a ticker", 0.5, None, 0.5, fut),
                     ("KXNBAGAME-26OCT29CCCDDD-CCC", 1.2, None, 0.5, fut),        # not a probability
                     ("KXNBAGAME-26OCT29EEEFFF-EEE", 0.5, None, 0.5)]),            # no start
            ("nba_deep", [("KXNBAGAME-26OCT29LALBOS-LAL", 0.62, None, 0.55, fut)]),
            ("zz_unregistered", [("KXNBAGAME-26OCT29LALBOS-LAL", 0.6, None, 0.55, fut)])])
        carried, _age = boardshare.get(predlog.CARRY_PREFIX + "guard", None)
        h1 = predlog._harvest_carried()
        h2 = predlog._harvest_carried()
        con = sqlite3.connect(predlog._DB)
        preds = con.execute("SELECT ticker, model, prob, event_ts FROM predictions ORDER BY ticker").fetchall()
        shadows = con.execute("SELECT ticker, model, prob FROM shadow").fetchall()
        # a newer version of the same file: logged again, the first forecast kept (first write wins)
        predlog.carry("guard", [("nba", [("KXNBAGAME-26OCT29LALBOS-LAL", 0.7, None, 0.55, fut)])])
        bump = time.time() + 5
        os.utime(boardshare._path(predlog.CARRY_PREFIX + "guard", "pkl"), (bump, bump))
        h3 = predlog._harvest_carried()
        lal = con.execute("SELECT prob FROM predictions WHERE ticker='KXNBAGAME-26OCT29LALBOS-LAL'").fetchone()
        con.close()
        saved_put = boardshare.put
        try:
            def _full(*a, **k):
                raise OSError("disk full")
            boardshare.put = _full
            n_fail = predlog.carry("guard2", [("nba", [("KXNBAGAME-26OCT29LALBOS-LAL", 0.6, None, 0.5, fut)])])
        finally:
            boardshare.put = saved_put
    ck("a builder carries its prediction rows beside its board (registered models only); the server logs the ones "
       "whose event START is known and still ahead, main models to predictions and shadows to shadow, once per "
       "file version",
       n_car == 7 and [g[0] for g in carried["groups"]] == ["nba", "nba_deep"] and h1 == 3 and h2 == 0
       and preds == [("KXNBAGAME-26OCT29LALBOS-BOS", "nba", 0.4, int(fut)),
                     ("KXNBAGAME-26OCT29LALBOS-LAL", "nba", 0.6, int(fut))]
       and shadows == [("KXNBAGAME-26OCT29LALBOS-LAL", "nba_deep", 0.62)], str((n_car, h1, h2, preds, shadows)))
    ck("...a newer carry is logged again but the first forecast stands, and a carry that cannot be written costs "
       "the build nothing",
       h3 == 1 and lal == (0.6,) and n_fail == 0, str((h3, lal, n_fail)))
    srcs = {m: open(os.path.join(_ROOT, f"{m}.py"), encoding="utf-8").read()
            for m in ("basket", "hockey", "nfl_game_sim", "cfb_board", "soccer", "golf")}
    flat = {m: " ".join(v.split()) for m, v in srcs.items()}
    ck("every board that logs inside its builder carries the same rows -- NBA/WNBA, NHL, NFL, college (moneyline and ATS; "
       "its total is Kalshi's since 2026-10-29), soccer, and golf since the accuracy batch (its start ESPN's event date) -- "
       "with the start as the fifth element; the book's line rides with them (accuracy_gaps.md 13.1, shadows *_book)",
       'predlog.carry(f"bk_{lg}_{date}", [(cfg["cal"], log_rows), ("nba_deep", deep_rows), (f"{lg}_book", book_rows)])'
       in flat["basket"]
       and 'predlog.carry(f"nhl_{date}", [("nhl", log_rows), ("nhl_deep", deep_rows), ("nhl_book", book_rows)])'
       in flat["hockey"]
       and '[("nfl_pre" if preseason else "nfl", log_rows), ("nfl_deep", deep_rows), ("nfl_book", book_rows)])'
       in flat["nfl_game_sim"]
       and 'predlog.carry(f"cfb_slate_{season}_w{week}", [("cfb", log_rows), ("cfb_ats", ats_rows), ("cfb_book", book_rows)])'
       in flat["cfb_board"]
       and '[("soccer", log_rows), ("soccer_dk", dk_rows), ("soccer_btts", btts_rows)])' in flat["soccer"]
       and 'predlog.carry(f"golf_{tour}", [("golf", rows)])' in flat["golf"]
       # the start rides on the model's row, the deep engine's and the book's
       and srcs["basket"].count('predlog.iso_ts(g.get("date"))') == 3
       and srcs["hockey"].count('predlog.iso_ts(g.get("date"))') == 3
       and srcs["soccer"].count(", ko))") == 3
       and predlog._harvest_carried in predlog._HARVESTERS
       and predlog.iso_ts("2026-10-29T23:30Z") == 1793316600.0 and predlog.iso_ts("junk") is None)

    # ==== the Matchups record ==================================================================
    now = time.time()
    # the side a line's labels speak about, read off the engines' OWN label functions
    mk_cheap_yes = _mkt("KXNFLRECYDS-26NOV01AAABBB-AAAPL1-40", 10, 92, now - 30)
    nfl_fit_no = _nfl_line(40, 0.25, mk_cheap_yes, now, shift=-nfl_matchup.STRONG_FIT)
    nba_ln = _nba_line(40, 0.25, _mkt("KXNBAPTS-26OCT29AAABBB-AAAPL1-40", 10, 92, now - 30), now, fit_side="no")
    ck("a pick is graded on the side its labels read: the NFL's strong-fit side (here NO, high probability at 75%, "
       "while the cheap YES has the larger EV -- the first draft graded best_side), NBA's larger-EV side",
       nfl_fit_no["value"]["best_side"] == "yes" and nfl_fit_no["fit_side"] == "no"
       and "high_probability" in nfl_fit_no["labels"] and R.label_side(nfl_fit_no, "nfl") == "no"
       and "positive_ev" in nba_ln["labels"] and R.label_side(nba_ln, "nba") == "yes"
       and R.label_side({"value": None, "fit_side": None}, "nba") == "yes",
       str((nfl_fit_no["labels"], nba_ln["labels"])))

    # one sighting: what it keeps
    prot = dict(nba_ln, market=dict(nba_ln["market"], ticker="KXNBAPTSPROT-26OCT29AAABBB-AAAPL1-40"))
    tail = _nba_line(45, 0.02, _mkt("KXNBAPTS-26OCT29AAABBB-AAAPL1-45", 2, 99, now - 30, volume=0), now)
    # 98% with the cheap side YES: labelled (high_probability; the +EV is withheld as thin_book on an untraded book)
    tail_hp = _nba_line(5, 0.98, _mkt("KXNBAPTS-26OCT29AAABBB-AAAPL1-5", 90, 12, now - 30, volume=0), now)
    stale = dict(nba_ln, value=dict(nba_ln["value"], quote_age_s=R.QUOTE_MAX_AGE_S + 1))
    s_ok = R._sighting(nba_ln, now - 10, "nba")
    s_tail, s_hp = R._sighting(tail, now, "nba"), R._sighting(tail_hp, now, "nba")
    s_stale = R._sighting(stale, now, "nba")
    ev_yes = PV.ev_buy(0.5, 10, 1.0)
    ck("a sighting keeps the engine's figure, Kalshi's de-vigged mid only off a believable two-sided book, and a cost "
       "only for a +EV line on a fresh quote (the side's ask plus Kalshi's fee); the PROT twins and an unlabelled "
       "line outside 5-95% are not recorded",
       R._sighting(prot, now, "nba") is None and s_tail is None and s_hp is not None and s_hp["mkt"] is None
       and "high_probability" in tail_hp["labels"] and "thin_book" in tail_hp["labels"] and s_hp["cost"] is None
       and s_ok["p"] == 0.25 and s_ok["mkt"] == nba_ln["value"]["p_market"] and s_ok["side"] == "yes"
       and abs(s_ok["cost"] - (ev_yes["price_c"] + ev_yes["fee_c"])) < 1e-9 and s_stale["cost"] is None
       and s_stale["mkt"] is None
       and R._has(s_ok["labels"], "positive_ev") and not R._has(s_ok["labels"], "market_better"),
       str((s_ok, s_hp)))
    ck("...and its moment is the LATEST of its quote, its build and the published file's server-side time (a PC "
       "whose clock runs slow cannot pass a build after the start off as pre-game); a stale quote gives no Kalshi "
       "figure (scored against the engine's fresh one it would tilt the comparison toward the engine)",
       s_ok["ts"] == int(now - 10) and R._sighting(nba_ln, now - 100, "nba")["ts"] == int(now - 30)
       and R._sighting(nba_ln, now - 100, "nba", now + 50)["ts"] == int(now + 50))

    with _sandbox():
        key = matchups.today_mt()
        start = now + 3600

        def ladder(shift, qts):
            return [_player("1", [_nba_line(k, round(min(0.95, max(0.05, p + shift)), 4),
                                            _mkt(f"KXNBAPTS-26OCT29AAABBB-AAAPL1-{k}", ya, 100 - ya + 2, qts), qts + 5)
                                  for k, p, ya in ((10, 0.85, 70), (15, 0.6, 50), (20, 0.35, 30))]
                                 + [_nba_line(25, 0.2, _mkt("KXNBAPTSPROT-26OCT29AAABBB-AAAPL1-25", 20, 82, qts), qts)]),
                    _player("2", [_nba_line(k, p, _mkt(f"KXNBAPTS-26OCT29AAABBB-AAAPL2-{k}", ya, 100 - ya + 2, qts),
                                            qts + 5)
                                  for k, p, ya in ((10, 0.7, 60), (15, 0.4, 40))])]
        _publish_index("nba", key, now - 600, start, ladder(0.0, now - 620))
        mt_first = boardshare.mtime(matchups.index_name("nba", key))
        h_first = R.harvest("nba", key, now)
        h_same = R.harvest("nba", key, now)
        bump = time.time() + 5
        mt_close = bump
        _publish_index("nba", key, now - 300, start, ladder(0.05, now - 320))
        os.utime(boardshare._path(matchups.index_name("nba", key), "pkl"), (bump, bump))
        h_close = R.harvest("nba", key, now)
        # after the start: nothing moves, even a build that read a quote from before the start
        bump += 5
        _publish_index("nba", key, start + 60, start, ladder(0.10, start - 100))
        os.utime(boardshare._path(matchups.index_name("nba", key), "pkl"), (bump, bump))
        h_after = R.harvest("nba", key, now)
        # a game whose start the index cannot give proves nothing pre-game
        bump += 5
        _publish_index("nba", key, now - 200, start, ladder(0.0, now - 220), games=[{"game_id": "401"}])
        os.utime(boardshare._path(matchups.index_name("nba", key), "pkl"), (bump, bump))
        h_nostart = R.harvest("nba", key, now)
        # the PC's clock 3 h slow: its stamps say pre-game, the file landed after the start
        bump = start + 120
        _publish_index("nba", key, now - 50, start, ladder(0.03, now - 60))
        os.utime(boardshare._path(matchups.index_name("nba", key), "pkl"), (bump, bump))
        h_slowpc = R.harvest("nba", key, now)
        con = R._conn()
        rows = {r["ticker"]: r for r in _rows(con, "SELECT * FROM mx_lines")}
        t10 = rows.get("KXNBAPTS-26OCT29AAABBB-AAAPL1-10") or {}
        ck("the record keeps ONE row a Kalshi ticker: the first pre-game sighting never changes, the last pre-game one "
           "replaces the close, an unchanged file is not re-read, and nothing seen at or after the start (a build "
           "after it, even off an earlier quote) or without a readable start is recorded",
           h_first == 5 and h_same == 0 and h_close == 5 and h_after == 0 and h_nostart == 0 and h_slowpc == 0
           and len(rows) == 5
           and not any("PROT" in t for t in rows) and t10.get("f_p") == 0.85 and t10.get("c_p") == 0.9
           and t10.get("f_ts") == int(mt_first) and t10.get("c_ts") == int(mt_close)
           and t10.get("event") == "KXNBAPTS-26OCT29AAABBB" and t10.get("game_ts") == int(start),
           str((h_first, h_same, h_close, h_after, h_nostart, sorted(rows))))
        # a write that fails is retried on the next pass rather than skipped for good
        bump = time.time() + 30
        _publish_index("nba", key, now - 100, start, ladder(0.02, now - 120))
        os.utime(boardshare._path(matchups.index_name("nba", key), "pkl"), (bump, bump))
        saved_conn = R._conn
        try:
            def _locked():
                raise sqlite3.OperationalError("database is locked")
            R._conn = _locked
            try:
                R.harvest("nba", key, now)
                failed = False
            except sqlite3.OperationalError:
                failed = True
        finally:
            R._conn = saved_conn
        h_retry = R.harvest("nba", key, now)
        ck("...a harvest whose write fails marks nothing seen: the next pass records that file version",
           failed and h_retry == 5, str((failed, h_retry)))

        # grading: one call per event, yes/no scored, scalar a void, the queue paced and never stuck
        con.execute("UPDATE mx_lines SET game_ts = game_ts - 86400, f_ts = f_ts - 86400, c_ts = c_ts - 86400")
        con.commit()
        calls = []
        settle = {"KXNBAPTS-26OCT29AAABBB-AAAPL1-10": {"result": "yes"},
                  "KXNBAPTS-26OCT29AAABBB-AAAPL1-15": {"result": "yes"},
                  "KXNBAPTS-26OCT29AAABBB-AAAPL1-20": {"result": "no"},
                  "KXNBAPTS-26OCT29AAABBB-AAAPL2-10": {"result": "scalar", "settlement_value_dollars": "0.6600"},
                  "KXNBAPTS-26OCT29AAABBB-AAAPL2-15": {"result": ""}}

        def fetch(ev):
            calls.append(ev)
            return {"markets": [dict(v, ticker=t) for t, v in settle.items()]}
        g1 = R.grade_due(now=now, fetch=fetch, pace_s=0)
        st1 = {r["ticker"][-5:]: (r["graded"], r["outcome"], r["settle_value"])
               for r in _rows(con, "SELECT * FROM mx_lines")}
        g_soon = R.grade_due(now=now + 60, fetch=fetch, pace_s=0)
        settle["KXNBAPTS-26OCT29AAABBB-AAAPL2-15"] = {"result": "no"}
        g_later = R.grade_due(now=now + R.RETRY_S + 1, fetch=fetch, pace_s=0)
        r10 = _rows(con, "SELECT * FROM mx_lines WHERE ticker='KXNBAPTS-26OCT29AAABBB-AAAPL1-10'")[0]
        ck("grading reads each event once, scores yes/no, files a player who never played (scalar, settled at the "
           "pre-game price) as a void, leaves an unsettled market for later, and does not ask again inside RETRY_S",
           g1 == 4 and calls[:1] == ["KXNBAPTS-26OCT29AAABBB"] and len(calls) == 2
           and st1["L1-10"] == (1, 1, None) and st1["L1-20"] == (1, 0, None) and st1["L2-10"] == (3, None, 0.66)
           and st1["L2-15"] == (0, None, None) and g_soon == 0 and g_later == 1,
           str((g1, calls, st1, g_soon, g_later)))
        cost10 = r10["c_cost"]
        ck("...each graded row stores its scores at grade time: log loss and Brier for the engine and the book, and "
           "the one-contract result on the side the labels read, fee in",
           abs(r10["c_ll_eng"] - (-math.log(r10["c_p"]))) < 1e-9
           and abs(r10["c_ll_mkt"] - (-math.log(r10["c_mkt"]))) < 1e-9
           and abs(r10["c_br_eng"] - (1 - r10["c_p"]) ** 2) < 1e-9
           and (cost10 is None or abs(r10["c_pnl"] - (100.0 - cost10)) < 1e-9 if r10["c_side"] == "yes"
                else abs(r10["c_pnl"] + cost10) < 1e-9), str({k: r10[k] for k in ("c_side", "c_cost", "c_pnl")}))
        # an event Kalshi will not answer for goes dead after DEAD_AFTER_S instead of heading the queue forever
        con.execute("INSERT INTO mx_lines (ticker, sport, stat, event, game_ts, graded) VALUES "
                    "('KXNBAPTS-26OCT01ZZZYYY-ZZZPL9-10', 'nba', 'pts', 'KXNBAPTS-26OCT01ZZZYYY', ?, 0)",
                    (int(now - R.DEAD_AFTER_S - 7200),))
        con.commit()

        con.execute("INSERT INTO mx_lines (ticker, sport, stat, event, game_ts, graded) VALUES "
                    "('KXNBAPTS-26OCT27XXXWWW-XXXPL9-10', 'nba', 'pts', 'KXNBAPTS-26OCT27XXXWWW', ?, 0)",
                    (int(now - 2 * 86400),))
        con.commit()
        boom_calls = []

        def boom(ev):
            boom_calls.append(ev)
            raise OSError("429")
        R.grade_due(now=now + 2 * R.RETRY_S, fetch=boom, pace_s=0)
        dead = _rows(con, "SELECT graded FROM mx_lines WHERE ticker='KXNBAPTS-26OCT01ZZZYYY-ZZZPL9-10'")[0]["graded"]
        later = _rows(con, "SELECT graded, tried_ts FROM mx_lines WHERE ticker='KXNBAPTS-26OCT27XXXWWW-XXXPL9-10'")[0]
        ck("...a market Kalshi will not answer for goes dead after DEAD_AFTER_S rather than heading the queue, and the "
           "first refused fetch ends the pass (kalshi._get_json already retried it three times: the rest of 40 events "
           "would be ~120 requests into a rate limit) -- the untried events lead the next pass",
           dead == 2 and boom_calls == ["KXNBAPTS-26OCT01ZZZYYY"] and later == {"graded": 0, "tried_ts": None},
           str((dead, boom_calls, later)))
        con.execute("DELETE FROM mx_lines WHERE ticker='KXNBAPTS-26OCT27XXXWWW-XXXPL9-10'")
        con.commit()
        con.execute("DELETE FROM mx_lines WHERE ticker='KXNBAPTS-26OCT01ZZZYYY-ZZZPL9-10'")
        con.commit()

        # the record: SQL sums only
        rec = R.record("nba")
        pts, pooled = rec["stats"].get("pts") or {}, rec["stats"].get("*") or {}
        blk = pts.get("close") or {}
        hand_eng = sum(r["c_ll_eng"] for r in _rows(con, "SELECT c_ll_eng FROM mx_lines WHERE graded=1"))
        ck("the record counts graded lines and voids apart, a player's ladder as ONE effective forecast, and scores "
           "the engine and Kalshi on the same lines",
           pts.get("graded") == 4 and pts.get("voids") == 1 and pooled.get("graded") == 4
           and blk.get("n") == 4 and blk.get("n_eff") == 2 and blk.get("days") == 1
           and abs(blk["vs_market"]["ll_eng"] - hand_eng / 4) < 1e-9 and blk["vs_market"]["n"] == 4
           and abs(blk["vs_market"]["diff"] - (blk["vs_market"]["ll_eng"] - blk["vs_market"]["ll_mkt"])) < 1e-9,
           str({k: blk.get(k) for k in ("n", "n_eff", "days", "vs_market")}))
        # a close more than CLOSE_WINDOW_S before the start is no close: first-sighting block only
        con.execute("UPDATE mx_lines SET c_ts = game_ts - ? WHERE ticker='KXNBAPTS-26OCT29AAABBB-AAAPL2-15'",
                    (R.CLOSE_WINDOW_S + 60,))
        con.commit()
        rec2 = R.record("nba")["stats"]["pts"]
        ck("...the close block reads only last sightings within 2 h of the start; the first-sighting block reads them all",
           rec2["close"]["n"] == 3 and rec2["first"]["n"] == 4, str((rec2["close"]["n"], rec2["first"]["n"])))
        # picks: shown vs muted by the market_better bit; high-probability graded on the label side
        con.execute("UPDATE mx_lines SET c_labels = c_labels | ? WHERE ticker='KXNBAPTS-26OCT29AAABBB-AAAPL1-20'",
                    (1 << R._LABEL_BITS.index("market_better"),))
        con.commit()
        rec3 = R.record("nba")["stats"]["pts"]["close"]
        ev_rows = _rows(con, "SELECT c_labels, c_pnl FROM mx_lines WHERE graded=1 AND c_pnl IS NOT NULL "
                             f"AND game_ts - c_ts <= {R.CLOSE_WINDOW_S}")
        mb = 1 << R._LABEL_BITS.index("market_better")
        ck("...+EV picks are split into shown and muted (market_better) with a game-clustered ROI, fee in",
           rec3["picks_ev"]["shown"]["n"] == sum(1 for r in ev_rows if not (r["c_labels"] & mb))
           and rec3["picks_ev"]["muted"]["n"] == sum(1 for r in ev_rows if r["c_labels"] & mb)
           and rec3["picks_ev"]["muted"]["n"] >= 1, str(rec3["picks_ev"]))
    with _sandbox():
        key = matchups.today_mt()
        start = now + 3600
        _publish_index("nfl", key, now - 600, start, [_player("9", [nfl_fit_no], stat="rec_yd")])
        R.harvest("nfl", key, now)
        con = R._conn()
        con.execute("UPDATE mx_lines SET game_ts = game_ts - 86400, f_ts = f_ts - 86400, c_ts = c_ts - 86400")
        con.commit()
        R.grade_due(now=now, fetch=lambda ev: {"markets": [{"ticker": mk_cheap_yes["ticker"], "result": "no"}]},
                    pace_s=0)
        hp = R.record("nfl")["stats"]["rec_yd"]["close"]["picks_hp"]
        # once a Platt is live the label was awarded on the TUNED figure: "said" reads it
        con.execute("UPDATE mx_lines SET c_pt = 0.2")
        con.commit()
        hp_t = R.record("nfl")["stats"]["rec_yd"]["close"]["picks_hp"]
        # one Mountain-time night: 5:30 pm and 7:30 pm MDT are two UTC dates and ONE sports day
        con.execute("UPDATE mx_lines SET game_ts = ?", (1793316600,))      # 2026-10-29 23:30 UTC, 17:30 MDT
        con.execute("INSERT INTO mx_lines (ticker, sport, stat, game_id, event, game_ts, graded, outcome, c_p, c_ts, "
                    "f_p, f_ts) VALUES ('KXNFLRECYDS-26OCT30ZZZYYY-ZZZPL1-40', 'nfl', 'rec_yd', '402', "
                    "'KXNFLRECYDS-26OCT30ZZZYYY', 1793323800, 1, 1, 0.5, 1793320000, 0.5, 1793320000)")
        con.commit()
        mt_days = R.record("nfl")["stats"]["rec_yd"]["first"]["days"]
    ck("...a high-probability pick is graded on the side it was made for: the NFL fit-side NO at 75% that settled NO "
       "is a hit",
       hp.get("n") == 1 and hp.get("hit") == 1.0 and abs((hp.get("pred") or 0) - 0.75) < 1e-9
       and abs((hp_t.get("pred") or 0) - 0.8) < 1e-9, str((hp, hp_t)))
    ck("the record's dates are Mountain-time sports days: a 5:30 pm and a 7:30 pm MDT game are two UTC dates and one "
       "day (UTC dates doubled the date floors and split one night across the forward chain's blocks)",
       mt_days == 1, str(mt_days))

    # the paired SEs, clustered by game
    m_se = R._se_paired([(0.3, 3), (-0.1, 2), (0.2, 5)])
    r_se = R._ratio_se([(5.0, 40.0), (-20.0, 50.0), (10.0, 30.0)])
    ck("the engine-vs-Kalshi difference and the picks' ROI carry SEs clustered by game (sums per game, delta method)",
       abs(m_se[0] - 0.04) < 1e-12
       and abs(m_se[1] - math.sqrt((0.3 - 0.12) ** 2 + (-0.1 - 0.08) ** 2 + (0.2 - 0.2) ** 2) / 10) < 1e-12
       and abs(r_se[0] - (-5 / 120)) < 1e-12 and R._se_paired([]) == (None, None) and R._ratio_se([]) == (None, None))

    # the tuning: out of sample, past floors, damped, monotone; the blend reported, never applied
    mis = _synth(0, -0.1, 0.6)
    t_mis = R.fit_stat(mis)
    full = R._fit_logit([(w, y, x, 0.0) for _d, _g, w, y, x, _m in mis], 2)
    lam = min(1.0, 20 / R.DAMP_DAYS)
    t_cal = R.fit_stat(_synth(100, 0.0, 1.0))
    t_rev = R.fit_stat(_synth(0, 0.0, -0.5))
    t_few = R.fit_stat(_synth(0, -0.1, 0.6, days=9))
    t_edge = R.fit_stat(_synth(4, -0.1, 0.6))           # a real effect this draw cannot prove: gain < 2 SE
    t_kept = R.fit_stat(_synth(4, -0.1, 0.6), prev={"family": "platt"})
    t_ten = R.fit_stat(_synth(0, -0.1, 0.6, days=10))
    t_eleven = R.fit_stat(_synth(0, -0.1, 0.6, days=11))
    early = [R.fit_stat(_synth(200 + sd, 0.0, 1.0, days=11))["family"] for sd in range(20)]
    ck("a stat's tuning is a Platt adopted only when it beats the engine OUT OF SAMPLE (forward chaining over dates, "
       "> MIN_GAIN and > 2 SE clustered by game), past FLOOR_NEFF ladders over FLOOR_DAYS dates, damped toward the "
       "identity by days / 30 -- an overconfident engine (slope 0.6) is pulled in",
       t_mis["family"] == "platt" and t_mis["gain"] > max(R.MIN_GAIN, 2 * t_mis["se"])
       and abs(t_mis["a"] - round(full[0] * lam, 4)) < 1e-9 and abs(t_mis["b"] - round(1 + (full[1] - 1) * lam, 4)) < 1e-9
       and 0.5 < full[1] < 0.8 and t_mis["n_eff"] == 800.0, str(t_mis))
    ck("...a calibrated engine keeps the identity, a fit that is not increasing in the engine's figure is refused "
       "(a band must stay a band), and below the floors nothing is fitted",
       t_cal["family"] == "identity" and t_cal["why"] == "did not beat the identity out of sample"
       and t_rev["family"] == "identity" and "not increasing" in t_rev["why"]
       and t_few["family"] == "identity" and t_few["why"] == "below the floors"
       and t_edge["family"] == "identity" and R.MIN_GAIN < t_edge["gain"] < 2 * t_edge["se"],
       str((t_cal.get("why"), t_rev.get("why"), t_few.get("why"), t_edge.get("gain"), t_edge.get("se"))))
    ck("...the gate reads at least 30 scored games (right after the date floor the chain scores a day or two: 2 SE "
       "over 4 games adopted a calibrated engine 6.5% of looks, a look every 6 h), so a calibrated engine at 11 days "
       "never adopts over a seed sweep and the reason says nothing was scored; at exactly 10 dates it is below the "
       "floors; and the decision is sticky -- a stat already tuned keeps its Platt while the gain stays above 0 and "
       "1 SE (here 1.8 SE), one on the identity needs 2 SE",
       early == ["identity"] * 20 and "nothing scored out of sample yet" in t_eleven["why"]
       and t_ten["why"] == "below the floors"
       and t_kept["family"] == "platt" and t_kept["kept"] is True and t_mis.get("kept") is False
       and se_ratio(t_kept) < 2 < se_ratio(t_mis), str((early, t_eleven.get("why"), t_kept)))
    dup = [(w / 2, y, x, 0.0) for _d, _g, w, y, x, _m in mis] * 2
    ck("...rows with the same outcome and figure are one weighted row (the server's pure-Python fit stays seconds "
       "for a season): the same fit exactly",
       max(abs(a - b) for a, b in zip(R._fit_logit(dup, 2), full)) < 1e-9)
    blend_rows = [(d, g, w, y, x, x + 0.3) for d, g, w, y, x, _m in mis]
    t_bl = R.fit_stat(blend_rows)
    ck("...the blend with Kalshi's mid is fitted and REPORTED, never applied (priced against the mid it was fitted "
       "to, its EV is ~0 by construction)",
       (t_bl.get("blend_report") or {}).get("applied") is False and t_bl["family"] in ("platt", "identity"))

    # the verdict that lifts or holds the "market better" mute
    def _verdict_on(days, games):
        """A book that IS the truth against an engine with noise on it: Kalshi better by 3.5-5 SE at every size
        below, so only the floors can leave a verdict undecided."""
        con = R._conn()
        R.init_db()
        con.execute("DELETE FROM mx_lines")
        rnd = random.Random(3)
        ins = []
        for d in range(days):
            for g in range(games):
                for i in range(20):
                    pt = rnd.uniform(0.2, 0.8)
                    y = rnd.random() < pt
                    pe = 1 / (1 + math.exp(-(math.log(pt / (1 - pt)) + rnd.gauss(0, 0.8))))
                    ins.append((f"KXNBAPTS-D{d}G{g}-P{i}-10", "nba", "pts", f"G{d}_{g}", 1_760_000_000 + d * 86400,
                                1_760_000_000 + d * 86400 - 600, pe, pt, int(y), -math.log(pe if y else 1 - pe),
                                -math.log(pt if y else 1 - pt), f"P{i}"))
        con.executemany("INSERT INTO mx_lines (ticker, sport, stat, game_id, game_ts, c_ts, c_p, c_mkt, outcome, "
                        "c_ll_eng, c_ll_mkt, player, graded) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)", ins)
        con.commit()
        v = R.verdict(con, "nba", "pts")
        v["record"] = (R.record("nba")["stats"]["pts"]["close"]["vs_market"] or {}).get("decided", "missing")
        return v
    with _sandbox():
        v_dec, v_days, v_games = _verdict_on(12, 3), _verdict_on(9, 4), _verdict_on(12, 2)
    ck("a stat's live verdict is decided only past the floors (10 dates AND 30 games) and by 2 SE clustered by game; "
       "under either floor it stays undecided however clear the gap (here 3.5-3.8 SE)",
       v_dec["decided"] == "market_better" and v_dec["games"] == 36 and v_dec["days"] == 12
       and v_dec["model_ll"] > v_dec["market_ll"]
       and v_days["decided"] is None and v_days["days"] == 9 and v_days["games"] == 36 and v_days["diff"] > 3 * v_days["se"]
       and v_games["decided"] is None and v_games["games"] == 24 and v_games["diff"] > 3 * v_games["se"],
       str((v_dec, v_days, v_games)))
    js_now = open(os.path.join(_ROOT, "static", "matchups.js"), encoding="utf-8").read()
    ck("...and the tab's bold call is that SAME decision (the record's vs_market carries it): a 2-SE gap under the "
       "floors reads 'not decided yet', never a bold call the mute itself refuses",
       [v["record"] for v in (v_dec, v_days, v_games)] == ["market_better", None, None]
       and 'v.decided === "market_better"' in js_now and "Math.abs(v.diff) > 2 * v.se" not in js_now)
    with _sandbox():
        calibrate.install_params(_params_blob(mx={"verdict": {"nfl": {
            "rec_yd": {"decided": "engine_better", "model_ll": 0.41, "market_ll": 0.43, "n": 900, "games": 60},
            "rush_yd": {"decided": None, "n": 12}}}}))
        live = matchups.market_record("nfl")
        pay = {"players": [{"markets": [{"stat": "rec_yd", "lines": [{"labels": ["positive_ev"]}]},
                                        {"stat": "rush_yd", "lines": [{"labels": ["positive_ev"]}]}]}]}
        n_marked = matchups.mark_record(pay, live, "nfl")
    ck("the tab's own decided verdict replaces the research figure for its stat (the mute lifts where the engine "
       "earned it); an undecided stat keeps the research prior and its mute",
       live["rec_yd"].get("live") is True and live["rec_yd"]["season"] == "live"
       and live["rush_yd"].get("season") != "live" and n_marked == 1
       and pay["players"][0]["markets"][0]["lines"][0]["labels"] == ["positive_ev"]
       and "market_better" in pay["players"][0]["markets"][1]["lines"][0]["labels"], str(live.get("rec_yd")))

    # applying the tuning at build time, with the engines' own value and label helpers
    mk_a = _mkt("KXNBAPTS-26OCT29AAABBB-AAAPL1-15", 55, 47, now - 20)
    nba_pay = {"players": [{"markets": [{"stat": "pts", "lines": [_nba_line(15, 0.62, mk_a, now)]},
                                        {"stat": "reb", "lines": [_nba_line(5, 0.7, None, now)]}]}]}
    tun = {"pts": {"family": "platt", "a": -0.2, "b": 0.8, "n_eff": 900, "days": 31, "gain": 0.004, "se": 0.001,
                   "fitted_ts": 1793300000},
           "reb": {"family": "identity", "a": 0.0, "b": 1.0, "why": "below the floors"}}
    n_tuned = R.apply_tuning(nba_pay, "nba", tun=tun, now=now)
    ln = nba_pay["players"][0]["markets"][0]["lines"][0]
    q = round(R._sig(-0.2 + 0.8 * R._lg(0.62)), 4)
    lo_q, hi_q = round(R._sig(-0.2 + 0.8 * R._lg(0.58)), 4), round(R._sig(-0.2 + 0.8 * R._lg(0.66)), 4)
    want_val = PV.line_value(q, mk_a, lo_q, hi_q, now=now)
    reb = nba_pay["players"][0]["markets"][1]["lines"][0]
    ck("a tuned stat's lines carry the tuned figure (the engine's kept as p_raw), the band mapped through the same "
       "monotone curve, and value and labels recomputed by the engine's own helpers; an untuned stat is untouched",
       n_tuned == 1 and ln["p_raw"] == 0.62 and ln["p"] == q and (ln["p_lo"], ln["p_hi"]) == (lo_q, hi_q)
       and ln["tuning"] == "platt:1793300000" and ln["value"]["ev_yes"] == want_val["ev_yes"]
       and ln["labels"] == nba_matchup._labels(q, lo_q, hi_q, 30.0, None, want_val)
       and "p_raw" not in reb and set(nba_pay["tuning"]) == {"pts", "reb"}, str(ln))
    nfl_pay = {"players": [{"markets": [{"stat": "rec_yd", "lines": [dict(nfl_fit_no)]}]}]}
    R.apply_tuning(nfl_pay, "nfl", tun={"rec_yd": dict(tun["pts"])}, now=now)
    nl = nfl_pay["players"][0]["markets"][0]["lines"][0]
    q2 = round(R._sig(-0.2 + 0.8 * R._lg(0.25)), 4)
    want = nfl_matchup.line_labels(q2, nl["p_lo"], nl["p_hi"], mk_cheap_yes, -nfl_matchup.STRONG_FIT, 40.0, now=now)
    ck("...the NFL's labels are re-read for the strong-fit side the line carries (fit_side), as its builder reads them",
       nl["p"] == q2 and nl["labels"] == want[1] and nl["value"]["ev_no"] == want[0]["ev_no"], str(nl["labels"]))
    ck("...and no adopted tuning leaves a board exactly as its engine built it, stamped with what was considered",
       R.apply_tuning({"players": [{"markets": [{"stat": "pts", "lines": [{"p": 0.5}]}]}]}, "nba", tun={}) == 0)

    # the plumbing: the index carries the tuned and raw figures, produce tunes, the loop is the owner's only
    idx = matchups.index_of({"tuning": {"pts": {"family": "platt"}}, "players": [
        {"player_id": "1", "game_id": "401", "markets": [{"stat": "pts", "lines": [dict(ln)]}]}]})
    il = idx["players"][0]["markets"][0]["lines"][0]
    app_src = open(os.path.join(_ROOT, "app.py"), encoding="utf-8").read()
    js = open(os.path.join(_ROOT, "static", "matchups.js"), encoding="utf-8").read()
    threads_before = {t.name for t in __import__("threading").enumerate()}
    R.start()
    ck("the index carries each line's tuned figure, the engine's and the tuning (the record harvests the index), the "
       "build tunes before it publishes, and only the background owner runs the record loop -- never a test",
       il.get("p_raw") == 0.62 and il.get("tuning") == "platt:1793300000" and idx.get("tuning") == {"pts": {"family": "platt"}}
       and "mx_record.apply_tuning(payload, sport)" in inspect.getsource(matchups.produce)
       and "if _BG_OWNER:\n        mx_record.start()" in app_src
       and "mx-record" not in threads_before and "mx-record" not in {t.name for t in __import__("threading").enumerate()})
    with _sandbox():
        r_bad = cl.get("/api/matchups/record?sport=mlb")
        r_nba = cl.get("/api/matchups/record?sport=nba")
    ck("/api/matchups/record answers per sport (nba / nfl) from SQL sums, and the tab has its Record view",
       r_bad.status_code == 400 and r_nba.status_code == 200 and "stats" in r_nba.get_json()
       and 'data-val="record"' in js and "mxRecordView()" in js and "/api/matchups/record?sport=" in js)
    # ==== the NFL record: Kalshi's own record on the same games ===============================
    import store
    with _sandbox() as tmp:
        saved_db = store.DB_PATH
        try:
            store.DB_PATH = os.path.join(tmp, "store.db")
            store.init_db()
            fut = time.time() + 3600
            picks = (("g1", "home", 0.62, "KXNFLGAME-26OCT25BBBAAA-AAA", 1, 0.58, None),    # fav (close) won
                     ("g2", "away", 0.55, "KXNFLGAME-26OCT25DDDCCC-DDD", 0, 0.48, 0.40),    # dog lost: fav won
                     ("g3", "home", 0.51, "KXNFLGAME-26OCT25FFFEEE-EEE", 0, 0.70, 0.66),    # fav lost
                     ("g4", "home", 0.60, "KXNFLGAME-26OCT25HHHGGG-GGG", 1, 0.50, None),    # 50%: no favourite
                     ("g5", "home", 0.70, "KXNFLGAME-26OCT25JJJIII-III", 1, None, None),    # no price: out
                     ("g6", "home", 0.66, "KXNFLGAME-26AUG20LLLKKK-KKK", 1, 0.60, None))    # preseason: out
            for gid, side, prob, tk, won, mkt, close in picks:
                store.record_nfl_pick(gid, "2026-10-25", 8, gid == "g6", side, gid.upper(), prob, 55.0, ticker=tk)
                store.set_nfl_grade(gid, won, "x")
                if mkt is not None:
                    predlog.log_many("nfl", [(tk, prob, None, mkt, fut)])
                if close is not None:
                    con = sqlite3.connect(predlog._DB)
                    con.execute("UPDATE predictions SET close_mkt=? WHERE ticker=?", (close, tk))
                    con.commit()
                    con.close()
            bench = store.market_benchmark("nfl")
            empty = store.market_benchmark("cfb")
        finally:
            store.DB_PATH = saved_db

    def _l(p, y):
        return -math.log(p if y else 1 - p)
    used = ((1, 0.62, 0.58), (0, 0.55, 0.40), (0, 0.51, 0.66), (1, 0.60, 0.50))
    ck("the NFL record carries Kalshi's own record on the SAME graded games: how often the market's favourite won "
       "and its log loss beside ours -- the pre-game close for the pick's side, else the logged price; a 50% game has "
       "no favourite; preseason and unpriced games stay out",
       bench.get("n") == 4 and bench.get("model_pct") == 50.0 and bench.get("fav_n") == 3
       and abs(bench.get("fav_pct", 0) - 66.7) < 0.05
       and abs(bench["ll_model"] - round(sum(_l(p, w) for w, p, _m in used) / 4, 4)) < 1e-9
       and abs(bench["ll_market"] - round(sum(_l(m, w) for w, _p, m in used) / 4, 4)) < 1e-9
       and empty == {"n": 0}
       and 'out["regular"]["market"] = store.market_benchmark("nfl")' in open(os.path.join(_ROOT, "app.py")).read()
       and "Kalshi's favourite" in open(os.path.join(_ROOT, "static", "app.js"), encoding="utf-8").read(), str(bench))
    import combo_engine
    ck("the NFL combo maker's game legs lean on the market: spread and total priors 0.3 / 0.2 (the sim's walk-forward "
       "weight beside the close was +0.25 then -0.06 for margins and negative for totals); the moneyline keeps its "
       "live fitted weight from predlog 'nfl'",
       combo_engine._MODEL_TRUST["nfl:Spread"] == 0.3 and combo_engine._MODEL_TRUST["nfl:Total"] == 0.2
       and combo_engine._TRUST_BUCKET.get("nfl:ML") == "nfl")

    wf = open(os.path.join(_ROOT, ".github", "workflows", "error-log.yml"), encoding="utf-8").read()
    ck("the snapshot workflow pulls the records to the data branch -- every predlog bucket against its Kalshi price, "
       "the NFL and college ledgers, the Matchups record per sport, the shared calibration file -- so a model change "
       "is checked against the live record without touching the server",
       all(x in wf for x in ('"sharp:/api/sharp"', '"nfl-record:/api/nfl/record"', '"cfb-record:/api/cfb/record"',
                             '"mx-record-nba:/api/matchups/record?sport=nba"',
                             '"mx-record-nfl:/api/matchups/record?sport=nfl"',
                             '"calib-params:/api/calibration/params"'))
       and 'd.pop("generated_ts", None)' in wf)
    import deep_cache
    saved = (R.harvest_all, R.grade_due, calibrate.params_due, deep_cache.run_child)
    tick_calls = []
    try:
        R.harvest_all = lambda now=None: 3
        R.grade_due = lambda now=None: 2
        calibrate.params_due = lambda: True
        deep_cache.run_child = lambda m, f, *a, **kw: tick_calls.append((m, f, kw.get("timeout"),
                                                                         deep_cache.HEAVY_BUILD._value))
        heavy_free = deep_cache.HEAVY_BUILD._value
        t_out = R.tick()
        calibrate.params_due = lambda: False
        R.tick()
    finally:
        R.harvest_all, R.grade_due, calibrate.params_due, deep_cache.run_child = saved
    ck("a record pass harvests, grades, and refits the shared file only when due, in a niced child under the "
       "heavy-build gate",
       t_out == (3, 2) and tick_calls == [("calibrate", "write_params", 1800, heavy_free - 1)], str(tick_calls))


def main():
    fails, n = [], [0]

    def ck(name, cond, detail=""):
        n[0] += 1
        if not cond:
            fails.append((name, detail))
            print(f"FAIL {name} {detail}")
    run(ck)
    print(f"guards_records: {n[0] - len(fails)}/{n[0]} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
