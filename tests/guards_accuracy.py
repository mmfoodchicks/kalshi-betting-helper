"""Guards for the accuracy batch (2026-10-29, research/reports/accuracy_gaps.md
13.1, agreed with a Fable agent): a sportsbook's line beside Kalshi's
(predlog.book_probs / book_row and the *_book shadow buckets of the NBA, NHL,
NFL and college boards), the NBA and NHL headlines on their measured trust
weight (model_trust.anchor / rung, read by the cards, best bets and the
cross-sport combo), the graded record's plumbing (UFC's market price and pure
model, golf's market, close, start and carry, tennis pre-match only with its
pure model, the record by series), ESPN's projections beside Sleeper's
(nfl_props_log, N1), and the DFS engine's own deep weight.
Hermetic: boardshare, predlog and the deep store point at a temporary
directory; every feed is a stub; nothing is fetched.

    python3 tests/guards_accuracy.py       # exits 1 on any failed check
    from tests.guards_accuracy import run  # run(ck) inside the main suite
"""
import contextlib
import datetime
import inspect
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import zoneinfo

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
_ET = zoneinfo.ZoneInfo("America/New_York")
_MON = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


@contextlib.contextmanager
def _sandbox():
    """predlog, boardshare and the deep store in a temp dir, calibrate's memos
    cleared; all restored on the way out (the main suite runs on after this)."""
    import boardshare
    import calibrate
    import deep_cache
    import predlog
    tmp = tempfile.mkdtemp()
    env = os.environ.get("CALIB_PARAMS")
    saved = (boardshare._DIR, predlog._DB, deep_cache.CACHE_DIR, dict(predlog._carry_seen),
             dict(calibrate._file_memo), dict(calibrate._cache))
    try:
        boardshare._DIR = os.path.join(tmp, "boards")
        predlog._DB = os.path.join(tmp, "predlog.db")
        deep_cache.CACHE_DIR = os.path.join(tmp, "deep")
        os.makedirs(deep_cache.CACHE_DIR, exist_ok=True)
        os.environ["CALIB_PARAMS"] = os.path.join(tmp, "calib_params.json")
        predlog._carry_seen.clear()
        calibrate._file_memo.update(path=None, mt=None, data=None)
        calibrate._cache.clear()
        predlog.init_db()
        yield tmp
    finally:
        boardshare._DIR, predlog._DB, deep_cache.CACHE_DIR = saved[0], saved[1], saved[2]
        for d, old in ((predlog._carry_seen, saved[3]), (calibrate._file_memo, saved[4]),
                       (calibrate._cache, saved[5])):
            d.clear()
            d.update(old)
        if env is None:
            os.environ.pop("CALIB_PARAMS", None)
        else:
            os.environ["CALIB_PARAMS"] = env
        shutil.rmtree(tmp, ignore_errors=True)


@contextlib.contextmanager
def _patched(*triples):
    """(obj, name, value) set for the block, restored after."""
    old = [(o, n, getattr(o, n)) for o, n, _v in triples]
    try:
        for o, n, v in triples:
            setattr(o, n, v)
        yield
    finally:
        for o, n, v in reversed(old):
            setattr(o, n, v)


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(ts))


def _tk_day(ts):
    """A ticker's YYMONDD for the Eastern day of `ts`."""
    d = datetime.datetime.fromtimestamp(ts, _ET)
    return f"{d:%y}{_MON[d.month - 1]}{d:%d}"


def _comp(state, provider, home, away):
    return {"status": {"type": {"state": state}},
            "odds": [{"provider": {"id": "100", "name": provider},
                      "moneyline": {"home": {"open": {"odds": "-110"}, "close": {"odds": home}},
                                    "away": {"open": {"odds": "-110"}, "close": {"odds": away}}}}]}


def _rows(db, sql, args=()):
    c = sqlite3.connect(db)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


# Four real ESPN week-9 projections (2026-10-29, the X-Fantasy-Filter read
# nfl_props_log makes): name, proTeamId, position id, appliedTotal, and the raw
# stats the mapping reads plus the ones ESPN's PPR also scores.
_ESPN_FIX = [
    ("Jahmyr Gibbs", 8, 2, 28.3115, {"23": 23.5153, "24": 108.9842, "25": 1.2356, "26": 0.0352, "42": 39.6655,
                                     "43": 0.204, "44": 0.0061, "53": 4.9626, "58": 5.8377, "72": 0.1214}),
    ("Puka Nacua", 14, 3, 23.2543, {"23": 0.8061, "24": 4.6207, "25": 0.0915, "26": 0.0042, "42": 100.9545,
                                    "43": 0.7301, "44": 0.0358, "53": 7.7902, "58": 10.9965, "72": 0.0528}),
    ("Brock Purdy", 25, 1, 21.9519, {"0": 30.6404, "1": 21.6442, "3": 264.9423, "4": 2.2113, "19": 0.0936,
                                     "20": 0.6477, "23": 4.4497, "24": 22.7647, "25": 0.2741, "26": 0.0083,
                                     "72": 0.1647}),
    ("Brock Bowers", 13, 4, 16.0258, {"42": 70.831, "43": 0.3882, "44": 0.0287, "53": 6.6282, "58": 9.3339,
                                      "72": 0.0367}),
]


def run(ck):
    import basket
    import boardshare
    import calibrate
    import hockey
    import model_trust
    import predlog

    # ==== C1: the book's line ==========================================================================
    bp = predlog.book_probs
    dk = bp(_comp("pre", "DraftKings", "-115", "-105"))
    h_raw, a_raw = 115 / 215, 105 / 205
    ck("a pre-game DraftKings moneyline is de-vigged off ESPN's latest (`close`) figure: the two sides' implied "
       "chances divided by their sum",
       dk is not None and abs(dk["home"] - h_raw / (h_raw + a_raw)) < 1e-12
       and abs(dk["home"] + dk["away"] - 1) < 1e-12 and dk["book"] == "DraftKings", str(dk))
    ck("the NHL's and college's spelling ('Draft Kings') and ESPN BET are books; a 'Live Odds' provider never is, "
       "even listed first beside a real book",
       bp(_comp("pre", "Draft Kings", "+150", "-170")) is not None
       and bp(_comp("pre", "ESPN BET", "+150", "-170")) is not None
       and bp(_comp("pre", "Live Odds", "+150", "-170")) is None
       and bp(_comp("pre", "DraftKings Live Odds", "+150", "-170")) is None
       and (bp({"status": {"type": {"state": "pre"}},
                "odds": _comp("pre", "Live Odds", "+400", "-600")["odds"] + _comp("pre", "DraftKings", "+150", "-170")["odds"]})
            or {}).get("book") == "DraftKings")
    ck("only a game ESPN still calls pre: in play and final are refused",
       bp(_comp("in", "DraftKings", "-115", "-105")) is None and bp(_comp("post", "DraftKings", "-115", "-105")) is None)
    ck("a line taken off the board ('OFF'), a pair whose chances sum under 1 (not one book's price) or over 1.25 "
       "is refused; EVEN is +100",
       bp(_comp("pre", "DraftKings", "OFF", "+120")) is None and bp(_comp("pre", "DraftKings", "+300", "+300")) is None
       and bp(_comp("pre", "DraftKings", "-400", "-400")) is None
       and abs(bp(_comp("pre", "DraftKings", "EVEN", "-120"))["home"] - 0.5 / (0.5 + 120 / 220)) < 1e-12)
    now = time.time()
    book = {"home": 0.58, "away": 0.42, "book": "DraftKings"}
    ck("a book row is the side's de-vigged chance in log_many's shape with Kalshi's price and the start beside it -- "
       "and nothing when the start is unknown or passed (the schedules are cached: a game they still call pre may "
       "have begun), there is no line, or Kalshi has no price beside it (the read compares the two in one row)",
       predlog.book_row("KXNBAGAME-X-AAA", book, "away", 123, 0.41, now + 60) == ("KXNBAGAME-X-AAA", 0.42, 123, 0.41, now + 60)
       and predlog.book_row("KXNBAGAME-X-AAA", book, "home", 123, 0.41, now - 1) is None
       and predlog.book_row("KXNBAGAME-X-AAA", book, "home", 123, 0.41, None) is None
       and predlog.book_row("KXNBAGAME-X-AAA", None, "home", 123, 0.41, now + 60) is None
       and predlog.book_row("KXNBAGAME-X-AAA", book, "home", 123, None, now + 60) is None)
    ck("the five book buckets carry as SHADOWS (graded off the model's row for the same ticker), golf as a bucket",
       all(predlog.CARRY_MODELS.get(m) is True for m in ("nba_book", "wnba_book", "nhl_book", "nfl_book", "cfb_book"))
       and predlog.CARRY_MODELS.get("golf") is False)

    # the schedule parsers read the line off the competition they already hold
    import nfl_live
    import racing
    ev = {"date": _iso(now + 86400), "competitions": [dict(_comp("pre", "DraftKings", "-192", "+160"), competitors=[
        {"homeAway": "home", "team": {"abbreviation": "GB", "id": "9", "shortDisplayName": "Packers"}, "score": "0"},
        {"homeAway": "away", "team": {"abbreviation": "CAR", "id": "29", "shortDisplayName": "Panthers"}, "score": "0"}])]}
    want = bp(ev["competitions"][0])
    with _patched((racing, "_get_json", lambda url, timeout=12, headers=None: {"events": [ev]}),
                  (racing, "_cached", lambda key, ttl, build: build())):
        s_nba = basket.schedule("nba", "2026-10-30")
        s_nhl = hockey.schedule("2026-10-30")
    s_nfl = nfl_live._parse_events([ev])
    ck("the NBA, NHL and NFL schedules carry the game's book line (no second ESPN call)",
       want and s_nba[0].get("book") == want and s_nhl[0].get("book") == want and s_nfl[0].get("book") == want,
       str((s_nba, s_nfl)))
    import nfl_data
    with _patched((nfl_data, "week_teams", lambda season, week: {"GB": {"abbr": "GB", "opp": "CAR"},
                                                                 "CAR": {"abbr": "CAR", "opp": "GB"}}),
                  (nfl_live, "schedule", lambda week, season, seasontype=2: s_nfl)):
        wg = nfl_data.week_games("2026", 9)
    ck("...and the NFL week's paired profiles carry it to the board (the home side's is the game's)",
       len(wg) == 1 and wg[0][0].get("book") == want and wg[0][1].get("book") == want, str(wg))
    import cfb
    import cfb_board
    cev = {"id": "401", "date": _iso(now + 86400), "competitions": [dict(_comp("pre", "Draft Kings", "-166", "+140"),
           neutralSite=False, competitors=[
               {"homeAway": "home", "team": {"id": "2641", "abbreviation": "TTU", "displayName": "Texas Tech"}},
               {"homeAway": "away", "team": {"id": "12", "abbreviation": "ARIZ", "displayName": "Arizona"}}])]}
    with _patched((racing, "_get_json", lambda url, timeout=12, headers=None: {"events": [cev]}),
                  (racing, "_cached", lambda key, ttl, build: build()),
                  (cfb, "teams", lambda season, which="all": {"2641": {"div": "fbs"}, "12": {"div": "fbs"}})):
        cw = cfb_board._week_games(2026, 9)
    ck("the college week's games carry it too",
       len(cw) == 1 and cw[0].get("book") == bp(cev["competitions"][0]) and (cw[0].get("book") or {}).get("home", 0) > 0.5,
       str(cw))
    import nfl_game_sim
    src_nfl = inspect.getsource(nfl_game_sim._build_board)
    src_cfb = inspect.getsource(cfb_board._build_board)
    ck("the NFL and college boards log the book's row in the same pass and at the same moment as the model's, for the "
       "same ticker, to their shadow buckets, and carry it beside the board (the NFL's regular season only)",
       'sim["_book"] = h.get("book")' in src_nfl
       and 'br = predlog.book_row(tk, sim.get("_book"), side, px.get("close"),' in src_nfl
       and "if br and not preseason:" in src_nfl
       and 'predlog.log_shadow("nfl_book", book_rows)' in src_nfl and '("nfl_book", book_rows)])' in src_nfl
       and 'br = predlog.book_row(tk, g.get("book"), side, px.get("close"),' in src_cfb
       and 'predlog.log_shadow("cfb_book", book_rows)' in src_cfb and '("cfb_book", book_rows)])' in src_cfb
       and src_cfb.index("log_rows.append((tk, p, px.get(\"close\"), predlog.devig(own, opp),")
       < src_cfb.index('br = predlog.book_row(tk, g.get("book")'))

    # ==== the NBA and NHL boards end to end: the anchor, the rungs, the record, the book ============
    weights = {"nba": 0.25, "nhl": 0.4}

    def _sim(*_a, **_k):
        return {"home": "AAA", "away": "BBB", "exp_home": 110.0, "exp_away": 105.0, "exp_total": 215.0,
                "mean_margin": 5.0, "total_ladder": [], "spread_ladder": [], "players": [], "props": [],
                "n_sims": 50, "p_home": 0.70, "_margins": list(range(-20, 30)),
                "_totals": [200 + i for i in range(50)]}

    def _markets(tag, start):
        day = _tk_day(start)
        return {"home_ml": {"cents": 60.0, "ticker": f"KX{tag}GAME-{day}BBBAAA-AAA", "close": start + 10800},
                "away_ml": {"cents": 42.0, "ticker": f"KX{tag}GAME-{day}BBBAAA-BBB", "close": start + 10800},
                "spreads": [{"side": "home", "line": 2.5, "cents": 50.0, "no_cents": 52.0, "city": "aaa"},
                            {"side": "away", "line": 4.5, "cents": 40.0, "no_cents": None, "city": "bbb"}],
                "totals": [{"line": 220.5, "cents": 55.0, "no_cents": 47.0}]}

    def _sched(start, past):
        g = {"home": "AAA", "away": "BBB", "home_name": "Aaa", "away_name": "Bbb", "date": _iso(start),
             "state": "pre", "detail": "", "home_score": None, "away_score": None, "book": dict(book)}
        # a game the cached schedule still calls pre whose start has passed: the model's row is the
        # board's business as before, the book's is refused
        g2 = dict(g, home="CCC", away="DDD", home_name="Ccc", away_name="Ddd", date=_iso(past))
        # a game whose moneyline has one ask quoted while its rungs are two-sided
        g3 = dict(g, home="EEE", away="FFF", home_name="Eee", away_name="Fff")
        return [g, g2, g3]

    import nba_deep_data
    import nhl_deep_data
    rt = {k: {"w": 1, "l": 1} for k in ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")}
    out = {}
    for lg in ("nba", "nhl"):
        start, past = time.time() + 3 * 86400, time.time() - 900
        tag = "NBA" if lg == "nba" else "NHL"
        mk = _markets(tag, start)
        mk_past = {k: (dict(v, ticker=v["ticker"].replace("BBBAAA", "DDDCCC")) if isinstance(v, dict) and "ticker" in v
                       else v) for k, v in _markets(tag, past).items()}
        mk_one = {k: (dict(v, ticker=v["ticker"].replace("BBBAAA", "FFFEEE")) if isinstance(v, dict) and "ticker" in v
                      else v) for k, v in _markets(tag, start).items()}
        mk_one["away_ml"] = dict(mk_one["away_ml"], cents=None)
        mod = basket if lg == "nba" else hockey
        deep = nba_deep_data if lg == "nba" else nhl_deep_data
        with _sandbox(), _patched(
                (mod, "schedule", (lambda lg_, date=None, s=start, p=past: _sched(s, p)) if lg == "nba"
                 else (lambda date=None, s=start, p=past: _sched(s, p))),
                (mod, "ratings", (lambda lg_=None: rt)),
                (mod, "players", (lambda lg_=None: {})),
                (mod, "simulate_game", _sim),
                (mod, "same_game_parlay", lambda sim, **k: None),
                (mod, "game_markets", (lambda *a: {"BBB": mk, "DDD": mk_past, "FFF": mk_one}[a[-1]])),
                (deep, "day_figures", lambda date, matchups: {}),
                (deep, "cached_day_figures", lambda date: {}),
                (calibrate, "apply", lambda m, p: p),
                (model_trust, "weight", lambda sport, default=None: weights[sport])):
            b = basket._build_board("nba", "2099-01-01", n=50) if lg == "nba" else hockey._build_board("2099-01-01", n=50)
            db = predlog._DB
            out[lg] = {"board": b, "start": start,
                       "preds": _rows(db, "SELECT ticker, model, prob, mkt FROM predictions ORDER BY ticker"),
                       "book": _rows(db, "SELECT ticker, model, prob, mkt FROM shadow WHERE model LIKE '%_book' ORDER BY ticker"),
                       "carry": boardshare.get(predlog.CARRY_PREFIX + ("bk_nba_2099-01-01" if lg == "nba"
                                                                       else "nhl_2099-01-01"), None)[0]}
    for lg in ("nba", "nhl"):
        w = weights[lg]
        # the stub sim names every game AAA-BBB: the schedule's names tell them apart
        g = next(x for x in out[lg]["board"]["games"] if x["home_name"] == "Aaa")
        pm = 60 / 102
        head = w * 0.70 + (1 - w) * pm
        ck(f"{lg.upper()}: the headline is w x model + (1 - w) x Kalshi's de-vigged moneyline at the league's MEASURED "
           "weight (model_trust.weight), the edges read it, and the model's own figure stays on the card",
           abs(g["p_home"] - round(head, 4)) < 1e-9 and g["p_home_ours"] == 0.7
           and g["anchor"] == {"w_model": w, "p_home_market": round(pm, 4)}
           and g["edge_home"] == round(head * 100 - 60, 1) and g["edge_away"] == round((1 - head) * 100 - 42, 1)
           and g["pick"]["team"] == "AAA", str({k: g.get(k) for k in ("p_home", "p_home_ours", "anchor", "edge_home")}))
        sp = {(r["side"], r["line"]): r for r in g["spread_edges"]}
        tot = g["total_edges"][0]
        p_sp, p_tot, p_aw = 27 / 50, 29 / 50, 16 / 50
        ck(f"{lg.upper()}: a spread or total rung reads the same weight against its OWN de-vigged price (yes / (yes + "
           "no) off its two asks), the YES ask where the book is one-sided; the model's figure rides beside it",
           abs(sp[("home", 2.5)]["pct"] - round(w * p_sp * 100 + (1 - w) * 100 * 50 / 102, 1)) < 1e-9
           and sp[("home", 2.5)]["model_pct"] == 54.0
           and abs(sp[("home", 2.5)]["edge"] - round(w * p_sp * 100 + (1 - w) * 100 * 50 / 102 - 50, 1)) < 1e-9
           and abs(sp[("away", 4.5)]["pct"] - round(w * p_aw * 100 + (1 - w) * 40, 1)) < 1e-9
           and abs(tot["pct"] - round(w * p_tot * 100 + (1 - w) * 100 * 55 / 102, 1)) < 1e-9 and tot["model_pct"] == 58.0,
           str((g["spread_edges"], g["total_edges"])))
        g3 = next(x for x in out[lg]["board"]["games"] if x["home_name"] == "Eee")
        sp3 = {(r["side"], r["line"]): r for r in g3["spread_edges"]}
        ck(f"{lg.upper()}: a one-sided moneyline leaves the headline the model's own (nothing to anchor to) but the "
           "rungs still read the league's weight -- the trust is the model's, not the book's",
           "anchor" not in g3 and g3["p_home"] == 0.7
           and abs(sp3[("home", 2.5)]["pct"] - round(w * p_sp * 100 + (1 - w) * 100 * 50 / 102, 1)) < 1e-9
           and abs(g3["total_edges"][0]["pct"] - round(w * p_tot * 100 + (1 - w) * 100 * 55 / 102, 1)) < 1e-9,
           str((g3.get("anchor"), g3["p_home"], g3["spread_edges"])))
        preds = {t: (m, p, mk_) for t, m, p, mk_ in out[lg]["preds"]}
        home_tk = f"KX{lg.upper()}GAME-{_tk_day(out[lg]['start'])}BBBAAA-AAA"
        ck(f"{lg.upper()}: the record still grades the MODEL -- the logged prediction is its raw figure, never the "
           "anchored headline -- beside Kalshi's de-vigged price",
           preds.get(home_tk) == (lg, 0.7, 60 / 102), str(preds))
        bk = out[lg]["book"]
        ck(f"{lg.upper()}: the book's de-vigged line is logged to {lg}_book for the same tickers with the same Kalshi "
           "price beside it, and a game whose start has passed logs no book row",
           sorted((t, m, p) for t, m, p, _ in bk) == [(home_tk, f"{lg}_book", 0.58),
                                                      (home_tk[:-4] + "-BBB", f"{lg}_book", 0.42)]
           and all(abs(mk_ - (60 / 102 if t.endswith("-AAA") else 42 / 102)) < 1e-9 for t, _m, _p, mk_ in bk),
           str(bk))
        groups = dict((m, r) for m, r in (out[lg]["carry"] or {}).get("groups") or [])
        ck(f"{lg.upper()}: the book rows ride beside the board with the model's (a PC build's only way into the record)",
           len(groups.get(f"{lg}_book") or []) == 2 and len(groups.get(lg) or []) == 6, str(list(groups)))

    # the carried book group reaches the server's shadow table
    with _sandbox():
        fut = time.time() + 7200
        predlog.carry("guard_book", [("nfl", [("KXNFLGAME-26NOV01BALBUF-BUF", 0.58, None, 0.61, fut)]),
                                     ("nfl_book", [("KXNFLGAME-26NOV01BALBUF-BUF", 0.604, None, 0.61, fut)])])
        predlog._harvest_carried()
        sh = _rows(predlog._DB, "SELECT ticker, model, prob, mkt FROM shadow")
    ck("a carried book group is logged by the server as a shadow, never as the ticker's prediction",
       sh == [("KXNFLGAME-26NOV01BALBUF-BUF", "nfl_book", 0.604, 0.61)], str(sh))

    # ==== the anchor's helpers ========================================================================
    with _patched((model_trust, "weight", lambda sport, default=None: 0.25)):
        an = model_trust.anchor(0.7, 60, 42, "nba")
        an_bad = [model_trust.anchor(0.7, *x, "nba") for x in ((None, 42), (0, 42), (100, 42), ("x", 42))]
    ck("model_trust.anchor: (headline, w, Kalshi's de-vigged home price); a one-sided or broken book anchors nothing",
       an is not None and abs(an[0] - (0.25 * 0.7 + 0.75 * 60 / 102)) < 1e-12 and an[1] == 0.25
       and abs(an[2] - 60 / 102) < 1e-12 and an_bad == [None] * 4, str((an, an_bad)))
    ck("model_trust.rung: w = 1 is the model alone; a two-sided rung de-vigs off its asks; a one-sided rung reads its ask",
       model_trust.rung(0.54, 1.0, 50, 52) == 54.0
       and abs(model_trust.rung(0.54, 0.25, 50, 52) - (13.5 + 0.75 * 5000 / 102)) < 1e-9
       and model_trust.rung(0.32, 0.25, 40, None) == 0.25 * 32 + 0.75 * 40)
    # the measured weights travel in the shared calibration file the PC fetches
    import errlog as _el
    import mx_record
    with _sandbox():
        model_trust.record("nba", 0.0, 304, "guard", extra={"odd": {1, 2}})    # a non-JSON extra
        with _patched((calibrate, "_MODELS", {}), (calibrate, "_blend_fit", lambda b: None),
                      (mx_record, "fit_all", lambda prev=None: {})):
            blob = calibrate.compute_params()
        try:
            blob_json = json.dumps(blob)       # a record's extras copied whole would not even write
        except TypeError:
            blob_json = None
        import deep_cache
        os.remove(deep_cache._path("model_trust"))             # this machine's store is gone ...
        if blob_json:
            calibrate.install_params(blob)                     # ... the server's file has arrived
        notes = []
        with _patched((_el, "note", lambda code, exc=None, msg="", path=None, detail=None: notes.append((code, msg)))):
            w_nba = model_trust.weight("nba")
            w_nhl = model_trust.weight("nhl")
            an_nhl = model_trust.anchor(0.6, 55, 47, "nhl")
            an_nba = model_trust.anchor(0.6, 55, 47, "nba")
    ck("the measured trust weights travel in the shared calibration file (plain numbers, whatever a backtest put in "
       "its record), and a machine whose own store lacks them anchors at the server's measurement -- the default "
       "only when neither has one, and then the ledger says so",
       blob.get("trust", {}).get("nba", {}).get("n") == 304 and "odd" not in blob["trust"]["nba"] and blob_json
       and w_nba == round(304 / 454 * 0.0 + 150 / 454 * 0.20, 3) and w_nhl == 0.20
       and an_nhl[1] == 0.20 and an_nba[1] == w_nba
       and [c for c, _m in notes] == ["TRUST-default"] and "nhl" in notes[0][1],
       str((blob.get("trust"), w_nba, w_nhl, notes)))

    import bestbets
    import combine
    fake = {"games": [{"state": "pre", "home": "AAA", "away": "BBB", "home_name": "Aaa", "away_name": "Bbb",
                       "p_home": 0.6162, "p_away": 0.3838, "kalshi": {"home_cents": 60, "away_cents": 42},
                       "spread_edges": [{"team": "AAA", "line": 2.5, "cents": 50, "model_pct": 54.0, "pct": 50.3,
                                         "edge": 0.3}],
                       "total_edges": [{"line": 220.5, "cents": 55, "model_pct": 58.0, "pct": 54.9, "edge": -0.1}],
                       "props": []}]}
    bb = {r["kind"]: r["our_pct"] for r in bestbets._board_rows(fake, "🏀 NBA")}
    legs = {lg_["type"]: lg_["prob"] for lg_ in combine._board_legs(fake, "🏀 NBA", "nba")}
    ck("best bets and the cross-sport combo read the rung's headline figure (`pct`), the same weight as the "
       "moneyline's, never the model's raw rung",
       bb.get("Spread") == 50.3 and bb.get("Total") == 54.9 and abs(legs.get("Spread") - 0.503) < 1e-12
       and abs(legs.get("Total") - 0.549) < 1e-12, str((bb, legs)))
    js = open(os.path.join(_ROOT, "static", "app.js")).read()
    ck("the NBA and NHL cards say what the headline is: our model, Kalshi's price, and the weight",
       js.count("the headline weighs our model at ${Math.round(g.anchor.w_model * 100)}%") == 2
       and "const shown = r.pct != null ? r.pct : r.model_pct;" in js)

    # ==== C2: UFC -- the market beside the fair figure, the pure model, pre-fight only ================
    import ufc_prices
    import ufc_sim
    t_fut, t_today = time.time() + 3 * 86400, time.time()
    tk_f = f"KXUFCFIGHT-{_tk_day(t_fut)}AAABBB"
    tk_t = f"KXUFCFIGHT-{_tk_day(t_today)}CCCDDD"
    mmap = {"fighter a": {"cents": 60.0, "ticker": tk_f + "-AAA", "close_time": None},
            "fighter b": {"cents": 45.0, "ticker": tk_f + "-BBB", "close_time": None},
            "fighter c": {"cents": 50.0, "ticker": tk_t + "-CCC", "close_time": None},
            "fighter d": {"cents": 52.0, "ticker": tk_t + "-DDD", "close_time": None}}

    def _ufc_board():
        def f(nm, wp):
            return {"name": nm, "win_pct": wp, "fights": 10}
        return {"bouts": [{"a": f("Fighter A", 70.0), "b": f("Fighter B", 30.0)},
                          {"a": f("Fighter C", 55.0), "b": f("Fighter D", 45.0)}]}
    with _sandbox(), _patched((ufc_prices, "_market_map", lambda: mmap), (ufc_sim, "board", _ufc_board),
                              (ufc_prices, "_model_weight_cap", lambda: 0.05)):
        att = ufc_prices.attach(_ufc_board())
        predlog._harvest_ufc()
        u_pred = _rows(predlog._DB, "SELECT ticker, model, prob, mkt FROM predictions ORDER BY ticker")
        u_ev = _rows(predlog._DB, "SELECT DISTINCT event_ts FROM predictions")
        u_sh = _rows(predlog._DB, "SELECT ticker, model, prob, mkt FROM shadow ORDER BY ticker")
    fa = att["bouts"][0]["a"]
    ck("UFC: every fighter carries the de-vigged market (`mkt_win`) the record reads -- 411 graded fights had none",
       fa.get("mkt_win") == round(100 * 60 / 105, 1) and att["bouts"][0]["b"].get("mkt_win") == round(100 * 45 / 105, 1),
       str(fa))
    ck("UFC: a fight is logged only before its card's day begins (midnight ET off the ticker; Kalshi's close_time is a "
       "backstop weeks later), with the market beside it, and the model's own figure goes to ufc_model beside the "
       "blend the bucket logs",
       [(t, m) for t, m, _p, _k in u_pred] == [(tk_f + "-AAA", "ufc"), (tk_f + "-BBB", "ufc")]
       and abs(u_pred[0][3] - 60 / 105) < 1e-3 and u_ev == [(int(predlog._event_ts(tk_f + "-AAA")),)]
       and [(t, m, p) for t, m, p, _k in u_sh] == [(tk_f + "-AAA", "ufc_model", 0.7), (tk_f + "-BBB", "ufc_model", 0.3)],
       str((u_pred, u_sh)))
    with _patched((model_trust, "load", lambda: {"weights": {}}),
                  (calibrate, "_params", lambda m: (1.0, 0.5, 0.0, 300))):
        cap300 = ufc_prices._model_weight_cap()
    ck("UFC: with no measured weight the fallback cap reads the graded count off calibrate's (t, q0, b, n) -- a "
       "three-name unpack raised and read 0 every time",
       abs(cap300 - 0.35) < 1e-12, str(cap300))

    # ==== C2: tennis pre-match only, the pure model beside the blend ===================================
    import tennis_prices

    def _tb():
        def m(tag, in_play, start=1_899_000_000):
            return {"in_play": in_play, "close_epoch": 1_900_000_000, "start_epoch": start,
                    "a": {"ticker": f"KXATPMATCH-26NOV05{tag}-A", "fair_win_raw": 60.0, "mkt_win": 55.0, "model_win": 70.0},
                    "b": {"ticker": f"KXATPMATCH-26NOV05{tag}-B", "fair_win_raw": 40.0, "mkt_win": 45.0, "model_win": 30.0}}
        return {"matches": [m("PRE", False), m("LIVE", True), m("NOSTART", False, start=None)]}
    with _sandbox(), _patched((tennis_prices, "board", _tb)):
        predlog._harvest_tennis()
        t_pred = _rows(predlog._DB, "SELECT ticker, model, prob, mkt FROM predictions ORDER BY ticker")
        t_sh = _rows(predlog._DB, "SELECT ticker, model, prob FROM shadow ORDER BY ticker")
    ck("tennis: a match already in play is never logged (its first sighting would file a live price as pre-match), "
       "nor one without a readable start (it proves nothing pre-match); the blend goes to tennis, the model's own "
       "figure to tennis_model",
       t_pred == [("KXATPMATCH-26NOV05PRE-A", "tennis", 0.6, 0.55), ("KXATPMATCH-26NOV05PRE-B", "tennis", 0.4, 0.45)]
       and t_sh == [("KXATPMATCH-26NOV05PRE-A", "tennis_model", 0.7), ("KXATPMATCH-26NOV05PRE-B", "tennis_model", 0.3)],
       str((t_pred, t_sh)))

    # ==== the record by series ======================================================================
    with _sandbox():
        c = sqlite3.connect(predlog._DB)
        rows = []
        for i in range(40):
            y = i % 2
            rows.append((f"KXATPMATCH-26NOV05A{i:03d}-X", "tennis", 0.7 if y else 0.3, 0.6 if y else 0.4, y))
            rows.append((f"KXITFMATCH-26NOV05B{i:03d}-X", "tennis", 0.4 if y else 0.6, 0.5, y))
        rows.append(("KXWTAMATCH-26NOV05C000-X", "tennis", 0.5, 0.5, 1))
        c.executemany("INSERT INTO predictions (ticker, model, prob, ts, mkt, graded, outcome) "
                      "VALUES (?, ?, ?, 0, ?, 1, ?)", rows)
        c.commit()
        c.close()
        bs = predlog.by_series("tennis")
        vm = predlog.vs_market("tennis")
        import app as A
        sharp = A.app.test_client().get("/api/sharp").get_json()
    import math as _m
    bs = {k: bs.get(k) or {} for k in ("KXATPMATCH", "KXITFMATCH", "KXWTAMATCH")}
    atp_m = -_m.log(0.7)
    atp_k = -_m.log(0.6)
    itf_m = -_m.log(0.4)
    ck("the record by series: each tour scored on its own rows (the same log loss and Brier as vs_market), a tour "
       "under 30 rows marked not ready -- tennis wins on the ATP here and loses on the ITF inside one bucket",
       bs["KXATPMATCH"].get("n") == 40 and abs(bs["KXATPMATCH"].get("model_logloss", 9) - round(atp_m, 4)) < 1e-9
       and abs(bs["KXATPMATCH"].get("market_logloss", 9) - round(atp_k, 4)) < 1e-9 and bs["KXATPMATCH"].get("edge", 0) > 0
       and abs(bs["KXITFMATCH"].get("model_logloss", 9) - round(itf_m, 4)) < 1e-9 and bs["KXITFMATCH"].get("edge", 0) < 0
       and bs["KXWTAMATCH"] == {"n": 1, "ready": False}
       and abs(bs["KXATPMATCH"].get("model_brier", 9) - 0.09) < 1e-9
       and vm["n"] == 81 and abs(vm["model_logloss"] - round((40 * atp_m + 40 * itf_m + _m.log(2)) / 81, 4)) < 1e-4,
       str((bs, vm)))
    ck("/api/sharp carries the split for the tennis buckets",
       ((sharp.get("models") or {}).get("tennis") or {}).get("by_series", {}).get("KXITFMATCH", {}).get("n") == 40,
       str(sharp)[:300])
    src_bs = inspect.getsource(predlog.by_series)
    ck("...summed in SQL (tens of thousands of graded rows), never pulled row by row",
       "GROUP BY series" in src_bs and "SUM(_ll(prob, outcome))" in src_bs)

    # ==== C2: golf -- the market, the close, the start, the carry ======================================
    import golf
    import kalshi
    mkts = [{"yes_sub_title": "Pat Two", "ticker": "KXPGAMAKECUT-MEX26-PTWO", "yes_ask_dollars": "0.75",
             "yes_bid_dollars": "0.71", "close_time": "2026-11-01T00:00:00Z"},
            {"yes_sub_title": "Lou One", "ticker": "KXPGAMAKECUT-MEX26-LONE", "yes_ask_dollars": "0.40",
             "yes_bid_dollars": "0.00", "close_time": "2026-11-01T00:00:00Z"}]
    gsim = {"_names": ["Pat Two", "Lou One"], "_finals": [], "_ids": [],
            "players": [{"name": "Pat Two", "make_cut_pct": 80.0}, {"name": "Lou One", "make_cut_pct": 30.0}]}
    with _patched((golf, "_kalshi_markets", lambda series: mkts if series == "KXPGAMAKECUT" else [])):
        pr = golf._price_board(gsim)
    mc = {r["ticker"]: r for r in pr["make_cut"]}
    ck("golf: a make-cut row carries the YES mid of a two-sided book and Kalshi's close; a one-sided book carries no "
       "price (an ask alone is an upper bound)",
       mc["KXPGAMAKECUT-MEX26-PTWO"]["mkt"] == 0.73 and mc["KXPGAMAKECUT-MEX26-LONE"]["mkt"] is None
       and mc["KXPGAMAKECUT-MEX26-PTWO"]["close"] == kalshi._parse_time("2026-11-01T00:00:00Z"), str(mc))

    def _golf_sim(state, start):
        return dict(gsim, event="E", course="C", state=state, start=start, round_now=1, n_rounds=4, cut_round=2,
                    cut_ahead=True, major=False, conditions={}, n_sims=10,
                    players=[{"name": "Pat Two", "to_par": 0, "position": "", "scoring_avg": 70.1, "win_pct": 1.0,
                              "top5_pct": 5.0, "top10_pct": 9.0, "top20_pct": 20.0, "make_cut_pct": 80.0}])
    priced = {"make_cut": [dict(mc["KXPGAMAKECUT-MEX26-PTWO"])], "h2h": []}
    got = {}
    for case, st, start in (("pre", "pre", time.time() + 86400), ("in", "in", time.time() + 86400),
                            ("started", "pre", time.time() - 60)):
        with _sandbox(), _patched((golf, "simulate", lambda tour, n=3000, s=st, t0=start: _golf_sim(s, t0)),
                                  (golf, "_price_board", lambda sim: priced)):
            golf._build_board("pga", n=10)
            got[case] = (_rows(predlog._DB, "SELECT ticker, prob, close_time, mkt, event_ts FROM predictions"),
                         (boardshare.get(predlog.CARRY_PREFIX + "golf_pga", None) or (None,))[0])
    pre_rows, pre_carry = got["pre"]
    ck("golf: logged before round one only (ESPN pre AND before the event's start, midnight ET of round one's day), "
       "with the price, the close and the start, and carried beside the board for the server (the PC builds golf)",
       len(pre_rows) == 1 and pre_rows[0][0] == "KXPGAMAKECUT-MEX26-PTWO" and pre_rows[0][1] == 0.8
       and pre_rows[0][3] == 0.73 and pre_rows[0][2] == kalshi._parse_time("2026-11-01T00:00:00Z")
       and pre_rows[0][4] is not None
       and pre_carry and pre_carry["groups"][0][0] == "golf" and len(pre_carry["groups"][0][1]) == 1
       and got["in"] == ([], None) and got["started"] == ([], None), str(got))
    ck("golf's start is ESPN's event date, parsed as an epoch",
       '"start": predlog.iso_ts(e.get("date")),' in inspect.getsource(golf.field)
       and '"start": fd.get("start"),' in inspect.getsource(golf.simulate))

    # ==== N1: ESPN's projection beside Sleeper's =======================================================
    import nfl_props_log as N
    raw_ok = True
    for name, _tid, _pos, applied, raw in _ESPN_FIX:
        g_ = lambda i: raw.get(i, 0.0)
        S = N.ESPN_STATS
        ppr = (0.04 * g_(S["pass_yd"]) + 4 * g_(S["pass_td"]) - 2 * g_("20") + 0.1 * g_(S["rush_yd"])
               + 6 * g_(S["rush_td"]) + g_(S["rec"]) + 0.1 * g_(S["rec_yd"]) + 6 * g_(S["rec_td"])
               + 2 * (g_("19") + g_("26") + g_("44")) - 2 * g_("72"))
        raw_ok = raw_ok and abs(ppr - applied) < 0.02
    gib, nac, pur = (dict(x[4]) for x in _ESPN_FIX[:3])
    S = N.ESPN_STATS
    ck("ESPN's stat ids MEAN what the store calls them: on four real week-9 projections the mapped components (with "
       "2-pt tries, interceptions and fumbles lost) rebuild ESPN's own PPR total; the running back's rushing yards "
       "dwarf his receiving and the receiver's the reverse; targets >= catches; the QB's attempts, ~8.6 yards each",
       raw_ok and gib[S["rush_yd"]] > 2 * gib[S["rec_yd"]] and nac[S["rec_yd"]] > 10 * nac[S["rush_yd"]]
       and nac[S["rec_tgt"]] >= nac[S["rec"]] and 25 < pur[S["pass_att"]] < 40
       and 7.0 < pur[S["pass_yd"]] / pur[S["pass_att"]] < 10.0 and 3.0 < gib[S["rush_yd"]] / gib[S["rush_att"]] < 6.0
       and set(S) == {"pass_att", "pass_yd", "pass_td", "rush_att", "rush_yd", "rush_td", "rec", "rec_yd",
                      "rec_td", "rec_tgt"} and set(S) | {"pts_ppr"} == set(N.SLEEPER_STATS))

    def _player(name, tid, pos, applied, raw, sid="1120269"):
        return {"player": {"fullName": name, "proTeamId": tid, "defaultPositionId": pos,
                           "stats": [{"id": "1120268", "appliedTotal": 99.0, "stats": {"3": 999.0}},
                                     {"id": sid, "appliedTotal": applied, "stats": raw}]}}
    calls = []
    pages = [[_player(*x) for x in _ESPN_FIX[:2]],
             [_player(*_ESPN_FIX[2]), _player("Kick Er", 8, 5, 9.0, {}), _player("Low Guy", 8, 3, 0.3, {"53": 0.1})],
             [_player(*_ESPN_FIX[3])]]

    def _get(url, hdr):
        f = json.loads(hdr["X-Fantasy-Filter"])["players"]
        calls.append((url, f))
        return {"players": pages[f["offset"] // f["limit"]]}
    with _patched((N, "ESPN_PAGE", 2)):
        rows = N.espn_projections(2026, 9, get=_get)
    f0 = calls[0][1]
    ck("ESPN's projections are read for the week asked, in pages sorted by that week's projection, stopping at the first "
       "page that reaches PROJ_FLOOR; only that week's projection entry is read (never another week's), kickers and "
       "below-floor players dropped, PPR from ESPN's own total",
       [r["name"] for r in rows] == ["Jahmyr Gibbs", "Puka Nacua", "Brock Purdy"] and len(calls) == 2
       and "seasons/2026/" in calls[0][0] and "scoringPeriodId=9&" in calls[0][0]
       and f0["filterStatsForTopScoringPeriodIds"]["additionalValue"] == ["1120269"]
       and f0["sortAppliedStatTotal"]["value"] == "1120269" and f0["sortAppliedStatTotal"]["sortAsc"] is False
       and f0["filterSlotIds"]["value"] == [0, 2, 4, 6] and calls[1][1]["offset"] == 2
       and rows[2]["stats"]["pass_yd"] == 264.942 and rows[2]["stats"]["pts_ppr"] == 21.952
       and rows[0]["pos"] == "RB" and rows[0]["team_id"] == "8" and "pass_yd" not in rows[0]["stats"],
       str((rows, [c[1].get("offset") for c in calls])))

    # the pass: stored for the teams still ahead of kickoff, frozen after, at most one ESPN read per window
    with _sandbox():
        t0 = 1_800_000_000.0
        kos = {"g1": t0 + 7200, "g2": t0 - 600}
        payload = {"season": {"year": 2026}, "week": {"number": 9}, "events": [
            {"id": "g1", "date": _iso(kos["g1"]), "competitions": [{"status": {"type": {"state": "pre"}},
                                                                   "venue": {"fullName": "X"}, "competitors": [
                {"homeAway": "home", "team": {"abbreviation": "DET", "id": "8"}},
                {"homeAway": "away", "team": {"abbreviation": "LAR", "id": "14"}}]}]},
            {"id": "g2", "date": _iso(kos["g2"]), "competitions": [{"status": {"type": {"state": "in"}},
                                                                   "venue": {"fullName": "Y"}, "competitors": [
                {"homeAway": "home", "team": {"abbreviation": "SF", "id": "25"}},
                {"homeAway": "away", "team": {"abbreviation": "LV", "id": "13"}}]}]}]}
        esp = [{"name": "Jahmyr Gibbs", "team_id": "8", "pos": "RB", "stats": {"rush_yd": 108.984}},
               {"name": "Brock Purdy", "team_id": "25", "pos": "QB", "stats": {"pass_yd": 264.942}},
               {"name": "Someone Onbye", "team_id": "30", "pos": "WR", "stats": {"rec_yd": 50.0}}]
        r1 = N.snapshot(now=t0, payload=payload, index={}, ladders=lambda s: {}, sleeper=[],
                        forecast=lambda g: {}, sims=[], espn=esp)
        st1, _ = __import__("deep_cache").load(N.KEY.format(season=2026, week=9))
        r2 = N.snapshot(now=t0 + 3 * 3600, payload=payload, index={}, ladders=lambda s: {}, sleeper=[],
                        forecast=lambda g: {}, sims=[], espn=[dict(esp[0], stats={"rush_yd": 1.0})])
        st2, _ = __import__("deep_cache").load(N.KEY.format(season=2026, week=9))
        fetched = []
        with _patched((N, "espn_projections", lambda season, week: fetched.append((season, week)) or esp),
                      (N, "_espn_last", [0.0])):
            store = {}
            n1 = N._espn(store, payload, {"DET", "LAR"}, t0, 2026, 9)
            n2 = N._espn(store, payload, {"DET", "LAR"}, t0 + N.ESPN_EVERY_S - 1, 2026, 9)
            n3 = N._espn(store, payload, {"DET", "LAR"}, t0 + N.ESPN_EVERY_S + 1, 2026, 9)
    ck("the pass keeps ESPN's projection beside Sleeper's for the players of a team still ahead of its kickoff (team "
       "ids off the week's own scoreboard; a game in play and a bye team keep nothing), and after kickoff rewrites "
       "nothing -- the last pre-game projection stays",
       r1["espn"] == 1 and set(st1["espn"]) == {"jahmyr gibbs"} and st1["espn"]["jahmyr gibbs"]["team"] == "DET"
       and st1["espn"]["jahmyr gibbs"]["stats"] == {"rush_yd": 108.984}
       and r2["espn"] == 0 and st2["espn"] == st1["espn"], str((r1, st1.get("espn"), r2)))
    ck("ESPN is read at most once every ESPN_EVERY_S (the fantasy API serves ~16 KB a player)",
       fetched == [(2026, 9), (2026, 9)] and n1 == 1 and n2 == 0 and n3 == 1 and N.ESPN_EVERY_S == 3 * 3600,
       str((fetched, n1, n2, n3)))
    ck("a failed ESPN read is ledgered and costs the pass nothing else",
       'errlog.note("NFLPROPS-espn", e)' in inspect.getsource(N._espn)
       and "espn_n = _espn(store, payload, live, now, int(season), int(week), espn) if live else 0"
       in inspect.getsource(N.snapshot))

    # ==== the DFS engine keeps the weight it was tested at =============================================
    import nfl_dfs_engine as DE
    with _patched((nfl_game_sim, "_DEEP_WEIGHT", 0.0)):
        bt = DE.blend_targets((20.0, 24.0), (30.0, 34.0))
    srcs = "".join(inspect.getsource(f) for f in (DE.blend_targets, DE.week_context, DE.sim_stamp))
    ck("the DFS engine blends its scoring level at its OWN 0.30 -- the weight its pre-registered 2025 test passed at -- "
       "so the slate headline's weight can be judged on the live record without moving a tested board",
       DE.DEEP_WEIGHT == 0.30 and abs(bt[0] - 23.0) < 1e-12 and abs(bt[1] - 27.0) < 1e-12
       and "nfl_game_sim._DEEP_WEIGHT" not in srcs, str(bt))


def main():
    fails, n = [], [0]

    def ck(name, cond, detail=""):
        n[0] += 1
        if not cond:
            fails.append((name, detail))
            print(f"FAIL {name} {detail}")
    run(ck)
    print(f"guards_accuracy: {n[0] - len(fails)}/{n[0]} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
