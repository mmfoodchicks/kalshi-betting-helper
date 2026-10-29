"""The Matchups tab's boards (/api/matchups): build in a child, publish in
pieces, serve the pieces.

A slate's matchup payload (the contract nba_matchup.board / nfl_matchup.board
return) is too big to serve whole: 2026-10-23's twelve NBA games came to
18.8 MB of JSON -- 260 players, ~56 matrix rows each -- and a boardshare read
unpickles the whole object inside the web worker that answers, on an instance
that has died of memory four times (CLAUDE.md). So a build publishes two kinds
of piece:

  matchups_<sport>_<key>             the slate INDEX: every player's lines as
                                     the scanner reads them (probability, band,
                                     Kalshi's quote, the EV block, labels) and
                                     nothing else -- no matrix, no profiles
  matchups_<sport>_<key>_g<game id>  one file PER GAME with the full contract
                                     for its players, fetched when the player
                                     page opens

The build itself (ESPN, Kalshi's index, the engine) runs in a niced child that
writes the per-game pieces and hands the parent only the index; nothing
CPU-bound or slate-sized lives in a gunicorn worker. The PC builds the same
pieces with the same function (pc_worker._task_boards) and its boards sync
uploads them; the server builds only when no fresh copy has arrived.

`key` is the slate's date (YYYY-MM-DD, the owner's Mountain-time day -- an NBA
night is the same date in MT and ET). A board is built only for today or
tomorrow: a past date's numbers would come from a book that already holds that
date's games (hit rates and profiles that saw the result), so a past date is
served from what was published at the time or not at all.
"""
import datetime
import os
import pickle
import subprocess
import sys
import time

import boardshare
import errlog

SPORTS = ("nfl", "nba")
# A board older than this is rebuilt on the next visit: Kalshi's quotes move and
# injury statuses change through the afternoon. The EV labels already refuse a
# quote older than 600 s (prop_value.LABEL_DEFAULTS), so an older board shows its
# EV muted rather than wrong.
TTL = 900
# The scanner's index keeps a line Kalshi does not list only while it says
# something (2% to 98%); the per-game piece keeps every line. Measured on
# 2026-10-23's NBA slate: the full ladder is 15,801 lines, most of them far
# tails of thresholds nobody prices.
_TAIL = 0.02

_LINE_KEYS = ("k", "p", "p_lo", "p_hi", "labels", "fit_side")
_MARKET_KEYS = ("ticker", "yes_bid", "yes_ask", "no_bid", "no_ask", "ts")
_VALUE_KEYS = ("best_side", "ev_yes", "ev_no", "ev_interval", "roi_yes", "roi_no", "edge", "p_market",
               "market_kind", "fee_assumed", "believable", "quote_age_s")
_TOP_KEYS = ("sport", "built_ts", "as_of", "provenance", "games", "notes", "definitions", "timing_s", "week",
             "season", "market_record")
_PLAYER_KEYS = ("player_id", "name", "team", "opp", "game_id", "pos", "status")

_MEM = {}          # boardshare.nonblocking's per-worker cache, pruned to _MEM_KEEP slates
_MEM_KEEP = 4


def index_name(sport, key):
    return f"matchups_{sport}_{key}"


def game_name(sport, key, game_id):
    return f"matchups_{sport}_{key}_g{game_id}"


def today_mt():
    import clock
    return clock.mt_date(time.time()).isoformat()


def buildable(key, today=None):
    """Only today and tomorrow (MT) are ever built (the module docstring)."""
    today = today or today_mt()
    try:
        d = datetime.date.fromisoformat(key)
        t = datetime.date.fromisoformat(today)
    except (TypeError, ValueError):
        return False
    return 0 <= (d - t).days <= 1


def _slim_line(ln):
    out = {k: ln[k] for k in _LINE_KEYS if k in ln}
    hs = ln.get("hit_similar_def") or {}
    if "n_eff" in hs:
        out["hit_similar_def"] = {"n_eff": hs.get("n_eff")}
    mk = ln.get("market")
    out["market"] = {k: mk.get(k) for k in _MARKET_KEYS} if mk else None
    v = ln.get("value")
    out["value"] = {k: v[k] for k in _VALUE_KEYS if k in v} if v else None
    return out


def _keep_in_index(ln):
    if ln.get("market"):
        return True
    p = ln.get("p")
    return isinstance(p, (int, float)) and _TAIL <= p <= 1.0 - _TAIL


def index_of(payload):
    """The scanner's index of a full payload (see the module docstring)."""
    out = {k: payload[k] for k in _TOP_KEYS if k in payload}
    err = str(payload.get("error") or "")
    if err and not any(err in str(n) for n in out.get("notes") or []):
        # an engine that cannot build (no committed model, no book yet) says why
        # in `error`; the tab reads notes, so the reason rides there too -- the
        # first NFL smoke build (2026-10-23) was an empty slate saying nothing
        out["notes"] = list(out.get("notes") or []) + [err]
    if payload.get("slate"):
        out["slate"] = payload["slate"]
    players = []
    for p in payload.get("players") or []:
        q = {k: p.get(k) for k in _PLAYER_KEYS}
        mins = p.get("minutes")
        if isinstance(mins, dict):
            q["minutes"] = {"projected": mins.get("projected")}
        q["markets"] = [{"stat": m.get("stat"), "baseline_mean": m.get("baseline_mean"),
                         "adjusted_mean": m.get("adjusted_mean"),
                         "lines": [_slim_line(ln) for ln in (m.get("lines") or []) if _keep_in_index(ln)]}
                        for m in (p.get("markets") or [])]
        players.append(q)
    out["players"] = players
    out["pieces"] = sorted({str(p.get("game_id")) for p in payload.get("players") or [] if p.get("game_id")})
    return out


# Each engine's held-out record against Kalshi's own pre-game prices, read from
# the committed files that produced its report (log loss on the same events;
# lower is better). A "+EV" line in a stat where the market's price was the
# better forecaster carries the qualifier "market_better": the EV is the model
# DISAGREEING with a forecaster that beat it, which the slate notes say once
# and every such chip now says where it is read. The 2026-10-25 NFL board
# labelled 786 lines +EV on a model Kalshi beat in every stat on 2026's frozen
# lines (0.456 vs 0.432; one-contract bets -7.0%).
_RECORD_FILES = {"nba": ("research", "data", "nba_matchup", "coefs.json"),
                 "nfl": ("research", "data", "nfl_matchup_model", "kalshi.json")}

def market_record(sport):
    """{stat: {"model_ll", "market_ll", "n", "season", "basis"}} for `sport`, with
    "*" the record a stat without its own falls back to; {} when no comparison
    exists. One population per sport, so a stat and the fallback are the same
    kind of number (the integration review's F17):
    NBA: coefs.json market_test (2025-26), the TIGHT books (spread <= 10c) for
    every stat -- the by-stat figures were measured over all two-sided books,
    where the report calls the two forecasters a tie (+0.0018 +/- 0.0020); on
    tight books the market is better (z -3.3), and +EV labels are only ever
    awarded on believable books. No tight by-stat record is committed.
    NFL: kalshi.json's latest season (2026, frozen pre-kickoff lines, books
    within 20c), by stat, all rungs as the fallback."""
    import json
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), *_RECORD_FILES.get(sport, ("-",)))
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as e:
        if sport in _RECORD_FILES:
            errlog.note("MX-record", e, path=sport)
        return {}
    out = {}
    if sport == "nba":
        mt = raw.get("market_test") or {}
        t = mt.get("tight") or {}
        if t.get("n"):
            out["*"] = {"model_ll": t["ll_p"], "market_ll": t["ll_mid"], "n": t["n"], "season": mt.get("season"),
                        "basis": "sampled Kalshi prop markets with tight pre-tip books (spread <= 10c), all stats"}
    elif sport == "nfl":
        seasons = raw.get("by_season") or {}
        if seasons:
            season = max(seasons)
            blk = seasons[season]
            for st, r in (blk.get("by_stat") or {}).items():
                if r.get("n"):
                    out[st] = {"model_ll": r["model"]["ll"], "market_ll": r["market"]["ll"], "n": r["n"], "season": season,
                               "basis": f"sampled Kalshi {st} rungs, last pre-kickoff hourly price, frozen pre-kickoff lines"}
            a = blk.get("all") or {}
            if a.get("n"):
                out["*"] = {"model_ll": a["model"]["ll"], "market_ll": a["market"]["ll"], "n": a["n"], "season": season,
                            "basis": "sampled Kalshi prop rungs, last pre-kickoff hourly price, frozen pre-kickoff lines"}
    return out


def mark_record(payload, record, sport=None):
    """Add "market_better" to the labels of every +EV line whose stat's record
    (or the fallback "*") has the market's log loss below the model's, and put
    the record on the payload for the tab's tooltip. In place; returns the
    number of lines marked. A sport that HAS a record file whose record comes
    back empty says so on the slate: no qualifier is silent flattery (F20)."""
    if not record:
        if sport in _RECORD_FILES:
            payload["notes"] = list(payload.get("notes") or []) + [
                "The engine's held-out record against Kalshi's prices could not be read: +EV labels below carry no "
                "market check (ledgered MX-record)."]
        return 0
    payload["market_record"] = record
    n = 0
    for p in payload.get("players") or []:
        for m in p.get("markets") or []:
            r = record.get(m.get("stat")) or record.get("*")
            if not r or not (r["market_ll"] < r["model_ll"]):
                continue
            for ln in m.get("lines") or []:
                labs = ln.get("labels")
                if isinstance(labs, list) and "positive_ev" in labs and "market_better" not in labs:
                    labs.append("market_better")
                    n += 1
    return n


def publish(sport, key, payload, with_index=True):
    """Write the per-game pieces, then (with_index) the index. The pieces go
    first so an index reader never names a game whose piece is missing.
    Returns the index."""
    mark_record(payload, market_record(sport), sport)
    no_game = [p for p in payload.get("players") or [] if not str(p.get("game_id") or "").isdigit()]
    if no_game:
        # the contract requires a game id; a piece named _gNone could never be
        # read by the route (digits only), so such a player would open to an error
        errlog.note("MX-nogame", msg=f"{sport} {key}: {len(no_game)} players without a game id dropped")
        payload["players"] = [p for p in payload["players"] if p not in no_game]
    by_game = {}
    for p in payload.get("players") or []:
        by_game.setdefault(str(p.get("game_id")), []).append(p)
    top = {k: payload[k] for k in _TOP_KEYS if k in payload}
    if payload.get("error"):
        top["notes"] = list(top.get("notes") or []) + [str(payload["error"])]
    for gid, players in by_game.items():
        boardshare.put(game_name(sport, key, gid), dict(top, game_id=gid, players=players))
    idx = index_of(payload)
    if with_index:
        boardshare.put(index_name(sport, key), idx)
    return idx


def produce(sport, key):
    """The sport's full payload for `key` (runs in the child)."""
    if sport == "nba":
        import nba_matchup
        return nba_matchup.board(key)
    if sport == "nfl":
        import nfl_matchup
        return nfl_matchup.board_for_date(key)
    raise ValueError(f"no matchup engine for {sport!r}")


def _child(raw):
    job = pickle.loads(raw)
    payload = produce(job["sport"], job["key"])
    return pickle.dumps(publish(job["sport"], job["key"], payload, with_index=False),
                        protocol=pickle.HIGHEST_PROTOCOL)


def build_isolated(sport, key, timeout=240):
    """produce + publish in a niced child; the parent receives only the index.
    A failed child returns None (ledgered MX-child)."""
    try:
        out = subprocess.run(
            [sys.executable, "-c", "import os\ntry:\n    os.nice(10)\nexcept (AttributeError, OSError):\n    pass\n"
             "import sys, matchups; sys.stdout.buffer.write(matchups._child(sys.stdin.buffer.read()))"],
            input=pickle.dumps({"sport": sport, "key": key}), cwd=os.path.dirname(os.path.abspath(__file__)),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout)
        if out.returncode == 0 and out.stdout:
            return pickle.loads(out.stdout)
        errlog.note("MX-child", msg=f"{sport} {key}: child rc={out.returncode}")
    except Exception as e:
        errlog.note("MX-child", e, path=f"{sport}:{key}")
    return None


def job_isolated(module, func, timeout=3600):
    """`module.func()` in a niced child (the engines' nightly data jobs on the
    server, under deep_cache.HEAVY_BUILD held by the caller): its small return
    value comes back pickled, anything it fails on is ledgered MX-job and
    returns None. nfl_matchup_data.refresh is ~120 MB of RSS and minutes of
    CSV parsing, measured by its builder -- not for a gunicorn worker."""
    try:
        out = subprocess.run(
            [sys.executable, "-c", "import os\ntry:\n    os.nice(10)\nexcept (AttributeError, OSError):\n    pass\n"
             f"import sys, pickle, importlib; sys.stdout.buffer.write(pickle.dumps("
             f"getattr(importlib.import_module({module!r}), {func!r})()))"],
            cwd=os.path.dirname(os.path.abspath(__file__)), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout)
        if out.returncode == 0 and out.stdout:
            return pickle.loads(out.stdout)
        errlog.note("MX-job", msg=f"{module}.{func}: child rc={out.returncode}")
    except Exception as e:
        errlog.note("MX-job", e, path=f"{module}.{func}")
    return None


def nfl_data_job(log=None):
    """The `nfl_matchup` deep job (server and PC): nfl_matchup_data.refresh for
    the seasons its daily rule reads (the current one; the previous one too
    until October), after a one-time BACKFILL of whichever of the two seasons
    before the current one has no store at the current SCHEMA. The profiles
    read those two seasons back (nfl_matchup_profiles BACK 2, DECAY 0.5) and
    2026's coverage exists only in them -- nflverse publishes a season's
    participation after its postseason -- but the daily rule never builds them,
    so a fresh server or PC would serve profiles without them. A backfilled
    season's raw files are deleted once its store is built (the 2025
    participation file alone is 49 MB; the server's disk is 1 GB).

    The rushing layer (nfl_rush_data) keeps an auxiliary store per season whose
    pbp extras ALIGN to the matchup store's rows, so the two must come from the
    same pbp download. A prior season is therefore backfilled when EITHER store
    is missing or its aux was built on another matchup store. Its raw files are
    removed first, so both stores are rebuilt from one fresh download: had the
    files survived a killed run, the matchup store would see nothing changed
    and keep rows the new pbp no longer has. Seasons go one at a time, each
    season's files dropped before the next downloads (~77 MB at a time, not
    ~154), and again in a `finally`, so a failed run never strands them; a
    failed aux is retried the next day because it still does not match.
    Keying the rushing backfill on the matchup store alone, as first proposed,
    never built 2024-25's aux on an instance whose matchup backfill had already
    run (research/reviews/nfl_rush_data_*.md)."""
    import clock
    import nfl_matchup_data as D
    try:
        import nfl_rush_data as R
    except Exception as e:          # the matchup data never waits on the rushing layer
        errlog.note("RUSH-import", e)
        R = None
    cur = D._current_season()
    daily = D.default_seasons(cur, clock.today_et().month)

    import deep_cache

    def rush_due(s):
        # missing, built on another store, or saved without a source the season should have (a
        # failed download): an incomplete aux still "matches" its store, and was never retried
        # (the data re-checks' Sonnet R1 / Opus R3)
        if R is None:
            return False
        aux = R.load_aux(s)
        return not R.aux_matches(aux, D.load_season(s)) or not R.aux_complete(aux)

    def due(s):
        if D.load_season(s) is None:
            return True
        if not rush_due(s):
            return False
        # a rebuild only the rushing layer asked for is tried at most once a week: a
        # persistent failure would otherwise cost a ~77 MB download and a store rebuild
        # every day (the data re-check's R3)
        mark, _ts = deep_cache.load(_RUSH_TRY.format(season=s))
        return not (mark and time.time() - float(mark.get("ts") or 0) < _RUSH_RETRY_S)

    # a season the daily rule reads anyway is not backfilled: before October the
    # previous season would be built twice and its raw files fetched twice
    prior = [s for s in (cur - 2, cur - 1) if s not in daily]
    back = [s for s in prior if due(s)]
    out = {"backfill": None, "rush": None}
    rush = {}

    def rush_refresh(seasons, keep_raw):
        for s in seasons:
            try:
                rush[s] = R.refresh(seasons=[s], keep_raw=keep_raw, log=log)
            except Exception as e:  # one season's failure never costs another's, nor the matchup data's
                errlog.note("RUSH-refresh", e, path=str(s))
                rush[s] = {"error": f"{type(e).__name__}: {e}"}

    try:
        # one season at a time, its files dropped before the next downloads: two seasons'
        # pbp + participation + FTN held at once are ~154 MB, one is ~77
        _drop_raw(D, R, back)
        for s in back:
            out["backfill"] = out["backfill"] or {}
            had_store = D.load_season(s) is not None
            if had_store:
                deep_cache.save(_RUSH_TRY.format(season=s), {"ts": time.time()})
            try:
                out["backfill"][s] = D.refresh(seasons=[s], keep_raw=True, log=log)
            except Exception as e:
                if not had_store:
                    raise           # the matchup data's own backfill fails the job, as it always did
                # a rebuild of a healthy store, asked for only by the rushing layer, never costs the
                # daily refresh (the data re-check's R2)
                errlog.note("NFLM-backfill", e, path=str(s))
                out["backfill"][s] = {"error": f"{type(e).__name__}: {e}"}
                _drop_raw(D, R, [s])
                continue
            if R is not None:
                rush_refresh([s], keep_raw=False)
            _drop_raw(D, R, [s])
        out.update(D.refresh(seasons=daily, log=log))
        if R is not None:
            rush_refresh(daily, keep_raw=True)
            # the rushing model's comparables history, computed here (PC first, else this niced
            # child) only when a shipped group reads it, so a board never pays its 103-136 s first
            # build on Render's one core (the rule re-check's Opus minor)
            try:
                import nfl_rush
                out["rush_vectors"] = nfl_rush.precompute_vectors(log=log)
            except Exception as e:  # the vectors never cost the matchup data
                errlog.note("NFLR-vectors", e)
    finally:
        _drop_raw(D, R, prior)
        out["rush"] = rush if R is not None else None
    return out


# The once-a-week marker of a rushing-only backfill attempt (a deep-store key, ~40 bytes).
_RUSH_TRY = "nfl_rush_backfill_try_{season}"
_RUSH_RETRY_S = 7 * 86400


def _drop_raw(D, R, seasons):
    """Remove the raw downloads of the seasons the daily rule does not read:
    the matchup store's pbp / participation / FTN and the rushing layer's PFR /
    injury / snap-count files. A file that is not there is the normal case; any
    other failure to remove one is ledgered, because on the server's 1 GB disk a
    stranded download is the next outage."""
    import deep_cache
    raw_dir = os.path.join(deep_cache.CACHE_DIR, "nfl_matchup_raw")
    kinds = ("pbp", "participation", "ftn") + (("pfr_rush", "pfr_def", "pfr_pass", "injuries", "snaps") if R else ())
    url_for = R.url_for if R is not None else D.url_for
    for s in seasons:
        for kind in kinds:
            path = os.path.join(raw_dir, os.path.basename(url_for(kind, s)))
            for p in (path, path + ".meta.json"):
                try:
                    os.remove(p)
                except FileNotFoundError:
                    pass
                except OSError as e:
                    errlog.note("RUSH-rm", e, path=p)


# A build that failed, or produced a slate with no players (no book yet, no
# committed model), is not tried again for this long: relaunching it on every
# visit started nine children in 170 s in review (F3).
_RETRY_S = 120


def _placeholder(sport, key, why):
    return {"sport": sport, "built_ts": time.time(), "players": [], "games": [], "pieces": [],
            "notes": [why], "placeholder": True}


def _build(sport, key):
    """The server's build (boardshare.nonblocking's builder). A full slate is
    returned and published fresh. An empty or failed one is NEVER published
    over a full board for the same date: when there is none it is published
    BACKDATED to TTL - _RETRY_S (it reads fresh for two minutes in every worker,
    then is rebuilt), and this worker remembers the failure so its visits serve
    what is on disk meanwhile; returns None so nothing newer is written."""
    import deep_cache
    name = index_name(sport, key)
    with deep_cache.HEAVY_BUILD:
        idx = build_isolated(sport, key)
    if idx is None:
        idx = _placeholder(sport, key, "This board's build failed this time (ledgered MX-child); it is tried again "
                                        "within two minutes of the next visit.")
    if idx.get("players"):
        return idx
    _MEM[("_mx_fail", name)] = (time.time(), idx)
    prev, _age = boardshare.get(name, None)
    if not (prev and prev.get("players")):
        boardshare.put(name, idx, age=TTL - _RETRY_S)
    return None


def pc_board(sport, key=None):
    """The PC's build (pc_worker._task_boards): synchronous, published only
    when it holds players. An empty slate built on a PC without the books (its
    first cycle runs the boards before the deep jobs) must never reach the
    server: the server adopts any boards file fresher than its own, and the PC
    can only add speed (CLAUDE.md; the integration review's F2). Returns the
    index, or a {"skipped": why} marker -- never None, which _task_boards reads
    as "still building" and re-invokes."""
    key = key or today_mt()
    name = index_name(sport, key)
    if not _in_season(sport):
        return {"skipped": "out of season"}
    have, _age = boardshare.get(name, TTL)
    if have is not None and have.get("players"):
        return have
    idx = build_isolated(sport, key)
    if idx and idx.get("players"):
        boardshare.put(name, idx)
        return idx
    return {"skipped": "nothing to publish: " + "; ".join((idx or {}).get("notes") or ["the build failed"])[:300]}


def _in_season(sport):
    try:
        import pro_sim
        return pro_sim.in_season(sport)
    except Exception as e:
        errlog.note("MX-season", e, path=sport)
        return True


def board(sport, key=None):
    """(index, age_s, building) for the scanner. A fresh index is served as is;
    a stale one is served while its rebuild runs; a date that is not buildable
    (module docstring) serves whatever was published for it; out of season
    nothing is built. `building` is this worker's view: the worker that started
    a rebuild says True, a sibling serving the same stale copy says False until
    its own next visit (gunicorn workers share the disk, not the flag)."""
    key = key or today_mt()
    name = index_name(sport, key)
    if not buildable(key):
        idx, age = boardshare.get(name, None)
        return idx, age, False
    if not _in_season(sport):
        idx, age = boardshare.get(name, None)
        return (idx or _placeholder(sport, key, f"The {sport.upper()} is out of season: no matchup board is built.")), age, False
    fail = _MEM.get(("_mx_fail", name))
    if fail and time.time() - fail[0] < _RETRY_S:
        idx, age = boardshare.get(name, None)
        return (idx or fail[1]), age, False
    for k in [k for k in _MEM if isinstance(k, str)][:-_MEM_KEEP]:
        _MEM.pop(k, None)           # dates do not repeat forever: keep the last few slates only
    for k in [k for k in _MEM if isinstance(k, tuple) and k[0] == "_mx_fail" and time.time() - _MEM[k][0] > _RETRY_S]:
        _MEM.pop(k, None)
    idx = boardshare.nonblocking(name, TTL, _MEM, name, lambda: _build(sport, key), note_id="MX-build")
    if idx is not None:
        hit = _MEM.get(name)
        return idx, (time.time() - hit[0]) if hit else 0.0, False
    stale, age = boardshare.get(name, None)
    return stale, age, bool(_MEM.get(("_bs_flight", name)))


def game(sport, key, game_id):
    """(full players of one game, age_s) as published with the index."""
    return boardshare.get(game_name(sport, key, str(game_id)), None)




# ---- the live view: a player leaves the list once his game starts ----------------------------
# The owner (2026-10-24): "when the specific player's game starts he drops from the
# matchup list until it's over and the next game is picked up" -- and then: "I only want
# it to show the next game starting at midnight, I dont want them tainting the pool of
# unplayed players". So today's board (Mountain time) shows only the players whose game
# has not started; the next day's board, with everyone's next game, takes over at
# midnight MT when today_mt() turns. Once a game kicks off Kalshi's quotes are in-game
# prices (kalshi_nba_props: the list endpoint keeps serving them during play), and a
# pre-game probability set against them is exactly the mistake the CLAUDE.md gotcha
# names. A game with no start time falls back to the state its board was built with.
def game_started(g, now):
    st = g.get("start_ts")
    if not isinstance(st, (int, float)):
        return str(g.get("state") or "").lower() in ("in", "post")
    return now >= st


def next_midnight_mt(now):
    """The epoch of the next midnight, Mountain time: when the next day's board takes over."""
    import clock
    d = clock.mt_date(now) + datetime.timedelta(days=1)
    return datetime.datetime.combine(d, datetime.time(0, 0), tzinfo=clock.MT).timestamp()


def drop_started(idx, now=None):
    """The index without the players of games already under way or over (a copy;
    the published board is never changed). Returns (index, started game ids)."""
    now = time.time() if now is None else now
    if not idx:
        return idx, set()
    gone = {str(g.get("game_id")) for g in idx.get("games") or [] if game_started(g, now)}
    out = dict(idx, live_filtered=True, next_day_ts=next_midnight_mt(now))
    out["players"] = [p for p in idx.get("players") or [] if str(p.get("game_id")) not in gone]
    out["games"] = [g for g in idx.get("games") or [] if str(g.get("game_id")) not in gone]
    if gone:
        n = len(gone)
        out["notes"] = list(idx.get("notes") or []) + [
            f"{n} game{'s' if n != 1 else ''} already under way or over: {'their' if n != 1 else 'its'} players are off "
            "the list (in-game prices are not pre-game prices). The next day's board shows from midnight, Mountain time."]
    return out, gone


def started_piece(piece, now=None):
    """True when a game's detail piece is for a game already under way or over: the
    route refuses it, as the index drops it (both live reviews' MAJOR: the piece of a
    board rebuilt after kickoff holds in-game quotes against the pre-game probability)."""
    now = time.time() if now is None else now
    gs = (piece or {}).get("games") or []
    gid = str((piece or {}).get("game_id") or "")
    mine = [g for g in gs if str(g.get("game_id")) == gid] or gs[:1]
    return bool(mine) and game_started(mine[0], now)
