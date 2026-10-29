"""The Matchups tab's record: every priced line graded off Kalshi's own
settlement, and the per-stat tuning it earns (2026-10-29).

The owner: "add a record to the match up tab for both nfl and NBA. I'd like it
to grade itself and tune itself based on results." Design and review:
research/reviews/matchups_record.md (a Fable agent: AGREE WITH CHANGES, every
change applied).

WHAT IT RECORDS. The server reads the published Matchups INDEX
(matchups.index_name) for the owner's today and tomorrow (MT) -- the same object
whoever built it, the server's child or the PC -- every ten minutes and only
when its file changed, and keeps ONE row per Kalshi market ticker: the FIRST
pre-game sighting, never changed again, and the LAST pre-game sighting, updated
until the game starts. Logging happens here, from the published board, never
inside a builder: a PC build logs into nothing the server grades (calibrate's
file note). A sighting is pre-game when its own time (the quote's `ts`, else the
index's build) is before the game's start: the published index keeps games
already under way (matchups.drop_started runs on serve only). Lines: Kalshi's
STANDARD series only (the *PROT twins refund an early exit at the pre-tip price,
kalshi_nba_props, so their scalar is not a void), with the engine's figure or
the book's in [0.05, 0.95], plus every labelled line.

GRADING. Kalshi settles every prop. A player who never plays settles SCALAR at
the pre-tip fair price -- a void here, counted and never scored, because every
engine probability is conditional on the player entering (verified 2026-10-29 on
KXNBAPTS-26OCT28LALLAC: 41 markets in ONE /markets?event_ticker= call, 12 yes,
25 no, Doncic's four rungs scalar at 0.82 / 0.66 / 0.43 / 0.28). One call per
event, paced, at most MAX_EVENTS a pass, kept apart from predlog.resolve_due so
thousands of prop tickers never starve the game records' 150-a-pass budget.

TUNING. Engine-only Platt per sport x stat (q = s(a + b logit p)) on graded
CLOSE sightings taken within CLOSE_WINDOW_S of the start, each rung weighted
1 / (rungs logged for its player-stat): a ladder is ONE forecast at several
thresholds, for the engine and the book alike, so the row count overstates the
evidence (the review's MAJOR 3). Adopted only when it beats the identity out of
sample -- forward chaining over blocks of dates, gain > MIN_GAIN nats a weighted
row and > 2 SE clustered by GAME -- after FLOOR_NEFF weighted rows over
FLOOR_DAYS dates, then damped toward the identity by days / 30. A blend with
Kalshi's mid is fitted and REPORTED, never applied: priced against the very mid
it was fitted to, its EV is ~0 by construction (MAJOR 2). Kalshi enters through
the existing "market better" mute instead (matchups.mark_record): a stat whose
live engine-vs-book record is decided past the floors replaces the static
research figure; an undecided one keeps it.

Everything heavy (fit_all) runs inside calibrate.write_params, in the background
owner's niced child; the record route is SQL sums over per-row contributions
computed at grade time -- never a fetchall of the table (CLAUDE.md).
"""
import math
import os
import sqlite3
import threading
import time

import errlog

SPORTS = ("nba", "nfl")
BAND = (0.05, 0.95)
CLOSE_WINDOW_S = 2 * 3600       # "close" = the last pre-game sighting within 2 h of the start
GRADE_AFTER_S = {"nba": 4 * 3600, "nfl": 5 * 3600}
DEAD_AFTER_S = 7 * 86400
RETRY_S = 1800                  # an event tried and not settled waits this long, so the queue moves on
MAX_EVENTS = 40
PACE_S = 1.0
FLOOR_NEFF = 300.0
FLOOR_DAYS = 10
MIN_GAIN = 0.001
DAMP_DAYS = 30.0
QUOTE_MAX_AGE_S = 600.0         # prop_value.LABEL_DEFAULTS: an older quote earns no pnl row and no book figure
MIN_SCORED_GAMES = 30           # a gate or a verdict reads at least this many games (the review's MAJOR 1)
# The owner's sports day: game_ts shifted 7 h, so every evening game in Mountain time keeps its own date
# (an NBA tip at 7:30 pm MDT is 01:30 UTC the next day; UTC dates split one night in two and doubled the
# date floors and the forward chain's blocks -- the build review's nit).
_DAY = "date(game_ts - 25200, 'unixepoch')"
_WHO = "COALESCE(NULLIF(player_id, ''), player)"   # a ladder's owner: the id, the name only when no id
_LABEL_BITS = ("high_probability", "positive_ev", "positive_ev_robust", "strong_fit", "low_evidence",
               "thin_book", "stale_quote", "market_better")
_lock = threading.Lock()


def db_path():
    import predlog
    return os.environ.get("MX_RECORD_DB") or os.path.join(
        os.path.dirname(os.path.abspath(predlog._DB)), "mx_record.db")


def _conn():
    c = sqlite3.connect(db_path(), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


_SIGHT = ("ts", "p", "p_lo", "p_hi", "pt", "fam", "mkt", "side", "cost", "labels", "qage")
_CONTRIB = ("ll_eng", "ll_tun", "ll_mkt", "br_eng", "br_mkt", "pnl")


def init_db():
    cols = []
    for s in ("f", "c"):
        cols += [f"{s}_ts INTEGER", f"{s}_p REAL", f"{s}_p_lo REAL", f"{s}_p_hi REAL", f"{s}_pt REAL",
                 f"{s}_fam TEXT", f"{s}_mkt REAL", f"{s}_side TEXT", f"{s}_cost REAL", f"{s}_labels INTEGER",
                 f"{s}_qage REAL"]
        cols += [f"{s}_{k} REAL" for k in _CONTRIB]
    with _lock, _conn() as c:
        c.execute(f"""CREATE TABLE IF NOT EXISTS mx_lines (
            ticker TEXT PRIMARY KEY, sport TEXT, stat TEXT, k REAL, player TEXT, player_id TEXT, team TEXT,
            game_id TEXT, event TEXT, game_ts INTEGER, {", ".join(cols)},
            graded INTEGER DEFAULT 0, outcome INTEGER, settle_value REAL, resolved_ts INTEGER, tried_ts INTEGER)""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_mx_grade ON mx_lines(graded, game_ts)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_mx_sport ON mx_lines(sport, stat, graded)")


def _bits(labels):
    return sum(1 << i for i, lab in enumerate(_LABEL_BITS) if lab in (labels or ()))


def _has(bits, label):
    return bool((bits or 0) & (1 << _LABEL_BITS.index(label)))


def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def _event(ticker):
    parts = str(ticker or "").split("-")
    return "-".join(parts[:2]) if len(parts) >= 3 else None


def label_side(line, sport):
    """The side a line's labels were read for -- the side its high_probability
    and positive_ev speak about, so the side a pick is graded on. Each engine's
    own rule: NBA's labels read the side with the larger EV
    (prop_value.line_labels' default; nba_matchup._labels), the NFL's the side a
    strong matchup fit favours, else that same default (nfl_matchup.line_labels);
    without a book, YES. The first draft graded every pick on best_side, which
    for an NFL strong-fit line can be the other side."""
    v = line.get("value") or {}
    if sport == "nfl" and line.get("fit_side") in ("yes", "no"):
        return line["fit_side"]
    return v.get("best_side") if v.get("best_side") in ("yes", "no") else "yes"


def _sighting(line, built_ts, sport, file_ts=None):
    """The columns one sighting of one line keeps, or None when it is not one
    to record (no standard-series market, outside the band and unlabelled).
    `file_ts`: the published file's mtime, the SERVER's clock (a PC upload is
    stamped with its build's age, X-Art-Age), so a PC whose clock runs slow
    cannot pass a build after the start off as pre-game."""
    mk = line.get("market") or {}
    tk = mk.get("ticker")
    if not tk or str(tk).split("-")[0].endswith("PROT"):
        return None
    p_tun = _num(line.get("p"))
    p_eng = _num(line.get("p_raw")) if line.get("p_raw") is not None else p_tun
    if p_eng is None or not (0.0 < p_eng < 1.0):
        return None
    v = line.get("value") or {}
    labs = [x for x in (line.get("labels") or []) if isinstance(x, str)]
    qage = _num(v.get("quote_age_s"))
    fresh = qage is not None and qage <= QUOTE_MAX_AGE_S
    # Kalshi's figure only off a believable two-sided book on a FRESH quote: a stale mid scored
    # against the engine's fresh figure would tilt "engine vs Kalshi" toward the engine
    mkt = (_num(v.get("p_market")) if (v.get("market_kind") == "two_sided" and v.get("believable") and fresh)
           else None)
    if mkt is not None and not (0.0 < mkt < 1.0):
        mkt = None
    labelled = any(x in labs for x in ("high_probability", "positive_ev", "positive_ev_robust"))
    inband = BAND[0] <= p_eng <= BAND[1] or (mkt is not None and BAND[0] <= mkt <= BAND[1])
    if not (inband or labelled):
        return None
    side = label_side(line, sport)
    cost = None
    if "positive_ev" in labs and fresh:
        ask = _num(mk.get(f"{side}_ask"))
        if ask is not None and 0.0 < ask < 100.0:
            import prop_value as PV
            e = PV.ev_buy(0.5, ask, 1.0)
            cost = (e["price_c"] + e["fee_c"]) if e else None
    # the sighting's moment is the LATER of its quote and its build: the engine's
    # figure exists only from the build, and a build can read a cached quote
    stamps = [t for t in (_num(mk.get("ts")), _num(built_ts), _num(file_ts)) if t]
    ts = max(stamps) if stamps else None
    return {"ts": int(ts) if ts else None, "p": p_eng, "p_lo": _num(line.get("p_lo")), "p_hi": _num(line.get("p_hi")),
            "pt": p_tun if p_tun is not None else p_eng, "fam": str(line.get("tuning") or "identity"),
            "mkt": mkt, "side": side, "cost": cost, "labels": _bits(labs), "qage": qage, "ticker": str(tk)}


_seen = {}            # index name -> the file version already harvested


def harvest(sport, key, now=None):
    """Record every pre-game sighting on the published index for (sport, key).
    Returns the number of lines offered (0 when the file is unchanged)."""
    import boardshare
    import matchups
    name = matchups.index_name(sport, key)
    mt = boardshare.mtime(name)
    if mt is None or _seen.get(name) == mt:
        return 0
    idx, _age = boardshare.get(name, None)
    if not idx or idx.get("placeholder") or idx.get("empty"):
        _seen[name] = mt
        return 0
    starts = {str(g.get("game_id")): _num(g.get("start_ts")) for g in idx.get("games") or []}
    built = _num(idx.get("built_ts")) or mt
    rows = []
    for pl in idx.get("players") or []:
        gid = str(pl.get("game_id"))
        start = starts.get(gid)
        if not start:
            continue                       # a start we cannot read cannot prove a sighting pre-game
        for m in pl.get("markets") or []:
            stat = m.get("stat")
            for ln in m.get("lines") or []:
                s = _sighting(ln, built, sport, mt)
                if s is None or not s["ts"] or s["ts"] >= start:
                    continue
                rows.append((s, stat, _num(ln.get("k")), pl, gid, start))
    if not rows:
        _seen[name] = mt
        return 0
    init_db()
    ins, upd = [], []
    for s, stat, k, pl, gid, start in rows:
        vals = [s[c] for c in _SIGHT]
        ins.append((s["ticker"], sport, stat, k, pl.get("name"), str(pl.get("player_id") or ""), pl.get("team"),
                    gid, _event(s["ticker"]), int(start), *vals, *vals))
        upd.append((*vals, s["ticker"], s["ts"], s["ts"]))
    fcols = ", ".join(f"f_{c}" for c in _SIGHT)
    ccols = ", ".join(f"c_{c}" for c in _SIGHT)
    marks = ", ".join("?" for _ in range(10 + 2 * len(_SIGHT)))
    sets = ", ".join(f"c_{c}=?" for c in _SIGHT)
    with _lock, _conn() as c:
        c.executemany(f"INSERT OR IGNORE INTO mx_lines (ticker, sport, stat, k, player, player_id, team, game_id, "
                      f"event, game_ts, {fcols}, {ccols}) VALUES ({marks})", ins)
        # the close: a later pre-game sighting replaces the last one, never a graded row
        c.executemany(f"UPDATE mx_lines SET {sets} WHERE ticker=? AND graded=0 AND ? > c_ts AND ? < game_ts", upd)
    _seen[name] = mt                   # only once written: a failed write is retried on the next pass
    return len(rows)


def harvest_all(now=None):
    import matchups
    now = time.time() if now is None else now
    total = 0
    today = matchups.today_mt()
    import datetime
    keys = (today, (datetime.date.fromisoformat(today) + datetime.timedelta(days=1)).isoformat())
    for sport in SPORTS:
        for key in keys:
            try:
                total += harvest(sport, key, now)
            except Exception as e:
                errlog.note("MXR-harvest", e, path=f"{sport} {key}")
    return total


# ---- grading -----------------------------------------------------------------
def _ll(p, y):
    p = min(1.0 - 1e-6, max(1e-6, p))
    return -math.log(p if y else 1.0 - p)


def _contrib(r, s, y):
    """The per-row scores of sighting `s` ("f" / "c") of a graded row."""
    p, pt, mkt = r[f"{s}_p"], r[f"{s}_pt"], r[f"{s}_mkt"]
    out = {"ll_eng": _ll(p, y), "ll_tun": _ll(pt if pt is not None else p, y),
           "ll_mkt": _ll(mkt, y) if mkt is not None else None,
           "br_eng": (p - y) ** 2, "br_mkt": (mkt - y) ** 2 if mkt is not None else None, "pnl": None}
    side, cost = r[f"{s}_side"], r[f"{s}_cost"]
    if side in ("yes", "no") and cost is not None:
        won = (y == 1) == (side == "yes")
        out["pnl"] = (100.0 - cost) if won else -cost
    return out


def grade_due(now=None, fetch=None, max_events=MAX_EVENTS, pace_s=PACE_S):
    """Grade rows whose game ended: one Kalshi call per event. Returns rows graded."""
    import kalshi
    now = time.time() if now is None else now
    fetch = fetch or (lambda ev: kalshi._get_json(f"{kalshi.BASE}/markets?event_ticker={ev}&limit=1000"))
    init_db()
    with _lock, _conn() as c:
        evs = [r["event"] for r in c.execute(
            "SELECT event, MIN(game_ts) g FROM mx_lines WHERE graded=0 AND event IS NOT NULL AND ("
            "(sport='nba' AND game_ts + ? < ?) OR (sport='nfl' AND game_ts + ? < ?)) "
            "GROUP BY event HAVING MAX(COALESCE(tried_ts, 0)) < ? ORDER BY g LIMIT ?",
            (GRADE_AFTER_S["nba"], now, GRADE_AFTER_S["nfl"], now, now - RETRY_S, int(max_events))).fetchall()]
    done = 0
    for i, ev in enumerate(evs):
        if i and pace_s:
            time.sleep(pace_s)
        failed = False
        try:
            d = fetch(ev) or {}
        except Exception as e:
            # an event Kalshi no longer answers for still goes dead below, or it
            # would head the oldest-first queue forever and starve newer events
            errlog.note("MXR-grade-fetch", e, path=ev)
            d, failed = {}, True
        mkts = {m.get("ticker"): m for m in d.get("markets") or [] if m.get("ticker")}
        with _lock, _conn() as c:
            rows = c.execute("SELECT * FROM mx_lines WHERE event=? AND graded=0", (ev,)).fetchall()
            for r in rows:
                m = mkts.get(r["ticker"])
                res = str((m or {}).get("result") or "").lower()
                if res in ("yes", "no"):
                    y = 1 if res == "yes" else 0
                    sets, vals = ["graded=1", "outcome=?", "resolved_ts=?"], [y, int(now)]
                    for s in ("f", "c"):
                        for k, v in _contrib(r, s, y).items():
                            sets.append(f"{s}_{k}=?")
                            vals.append(v)
                    c.execute(f"UPDATE mx_lines SET {', '.join(sets)} WHERE ticker=?", (*vals, r["ticker"]))
                    done += 1
                elif m is not None and res == "scalar":
                    # the player never entered: settled at the pre-game fair price -- a void, never scored
                    sv = _float(m.get("settlement_value_dollars"))
                    sv = sv if sv is not None else _float(m.get("settlement_value"))
                    c.execute("UPDATE mx_lines SET graded=3, settle_value=?, resolved_ts=? WHERE ticker=?",
                              (sv, int(now), r["ticker"]))
                    done += 1
                elif now > (r["game_ts"] or 0) + DEAD_AFTER_S:
                    c.execute("UPDATE mx_lines SET graded=2, resolved_ts=? WHERE ticker=?", (int(now), r["ticker"]))
            c.execute("UPDATE mx_lines SET tried_ts=? WHERE event=? AND graded=0", (int(now), ev))
        if failed:
            # kalshi._get_json already retried this one three times: a refusal now is a rate
            # limit or an outage, and 39 more events would be ~120 more requests into it. The
            # rest keep their tried_ts and lead the next pass.
            break
    return done


def _float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---- the record -----------------------------------------------------------------
def _decide(n, games, days, d, se):
    """Engine vs Kalshi decided? The ONE rule the tab's bold call and the live
    "market better" mute both read (the build review's MAJOR 2): past FLOOR_DAYS
    dates AND MIN_SCORED_GAMES games, and the paired difference beyond 2 SE
    clustered by game. d > 0: Kalshi's log loss is the lower."""
    if n and d is not None and se and days >= FLOOR_DAYS and games >= MIN_SCORED_GAMES and abs(d) > 2.0 * se:
        return "market_better" if d > 0 else "engine_better"
    return None


def _se_paired(groups):
    """(mean, se) of a per-row difference from per-game sums: groups = [(sum_d, n)].
    The SE is clustered by game: rows of one game share its script and its book."""
    n = sum(g[1] for g in groups)
    if n <= 0:
        return None, None
    mean = sum(g[0] for g in groups) / n
    var = sum((g[0] - mean * g[1]) ** 2 for g in groups)
    return mean, (math.sqrt(var) / n if len(groups) > 1 else None)


def record(sport):
    """The tab's record for one sport: per stat and pooled, SQL sums only."""
    init_db()
    out = {"sport": sport, "stats": {}, "generated_ts": int(time.time()),
           "definitions": {
               "graded": "lines Kalshi settled yes or no",
               "voids": "players who never played: Kalshi settled the line at the pre-game fair price; never scored",
               "close": f"the last pre-game sighting, only when within {CLOSE_WINDOW_S // 3600} h of the start",
               "log_loss": "lower is better; the engine and Kalshi's de-vigged mid on the SAME graded lines "
                           "(believable two-sided books only)",
               "picks": "+EV lines bought on the side and at the ask the tab showed, fee in, one contract each",
               "small_sample": f"fewer than {int(FLOOR_NEFF)} effective lines (a ladder counts once) or "
                               f"{FLOOR_DAYS} dates",
               "decided": f"engine vs Kalshi is called only past {FLOOR_DAYS} dates and {MIN_SCORED_GAMES} games "
                          "and beyond 2 SE clustered by game -- the rule the tab's 'market better' mute reads",
               "calibration": "the engine's own figure, before any tuning",
               "dates": "Mountain-time sports days"}}
    with _lock, _conn() as c:
        cols = f"COUNT(*) n, SUM(graded=3) voids, COUNT(DISTINCT game_id) games, COUNT(DISTINCT {_DAY}) days"
        base = {r["stat"]: dict(r) for r in c.execute(
            f"SELECT stat, {cols} FROM mx_lines WHERE sport=? AND graded IN (1,3) GROUP BY stat", (sport,))}
        base["*"] = dict(c.execute(f"SELECT '*' stat, {cols} FROM mx_lines WHERE sport=? AND graded IN (1,3)",
                                   (sport,)).fetchone())
        for stat in [st for st in base if st != "*"] + ["*"]:
            where = "sport=? AND graded=1" + ("" if stat == "*" else " AND stat=?")
            args = (sport,) if stat == "*" else (sport, stat)
            s = dict(base[stat])
            s["graded"] = (s.get("n") or 0) - (s.get("voids") or 0)
            for sg, lim in (("f", ""), ("c", f" AND game_ts - c_ts <= {CLOSE_WINDOW_S}")):
                # n_eff: a player's ladder in one game is ONE forecast (its rungs weigh 1/rungs, so
                # they sum to one), the unit the tuning's floors count
                r = c.execute(f"SELECT COUNT(*) n, COUNT(DISTINCT game_id || '|' || {_WHO} || '|' || stat) n_eff, "
                              f"COUNT(DISTINCT {_DAY}) days, AVG({sg}_p) p, AVG(outcome) hit, "
                              f"AVG({sg}_ll_eng) ll_eng, AVG({sg}_br_eng) br_eng FROM mx_lines WHERE {where}{lim}",
                              args).fetchone()
                blk = dict(r)
                # engine vs Kalshi, paired on rows with a believable book, SE clustered by game
                games = c.execute(f"SELECT SUM({sg}_ll_eng - {sg}_ll_mkt) d, COUNT(*) n, SUM({sg}_ll_mkt) m, "
                                  f"SUM({sg}_ll_eng) e FROM mx_lines WHERE {where}{lim} AND {sg}_ll_mkt IS NOT NULL "
                                  f"GROUP BY game_id", args).fetchall()
                d, se = _se_paired([(g["d"], g["n"]) for g in games])
                nm = sum(g["n"] for g in games)
                vdays = c.execute(f"SELECT COUNT(DISTINCT {_DAY}) FROM mx_lines WHERE {where}{lim} "
                                  f"AND {sg}_ll_mkt IS NOT NULL", args).fetchone()[0]
                blk["vs_market"] = {"n": nm, "games": len(games), "days": vdays,
                                    "ll_eng": (sum(g["e"] for g in games) / nm) if nm else None,
                                    "ll_mkt": (sum(g["m"] for g in games) / nm) if nm else None,
                                    "diff": d, "se": se, "decided": _decide(nm, len(games), vdays, d, se)}
                # the tuned figure, only where a non-identity tuning was in force at the sighting
                t = c.execute(f"SELECT COUNT(*) n, AVG({sg}_ll_tun) ll_tun, AVG({sg}_ll_eng) ll_eng FROM mx_lines "
                              f"WHERE {where}{lim} AND {sg}_fam != 'identity'", args).fetchone()
                blk["tuned"] = dict(t)
                picks = {}
                for muted in (0, 1):
                    bit = 1 << _LABEL_BITS.index("market_better")
                    pr = c.execute(f"SELECT COUNT(*) n, SUM({sg}_pnl > 0) won, SUM({sg}_pnl) pnl, "
                                   f"SUM({sg}_cost) cost FROM mx_lines WHERE {where}{lim} AND {sg}_pnl IS NOT NULL "
                                   f"AND (({sg}_labels & {bit}) != 0) = ?", (*args, muted)).fetchone()
                    pg = c.execute(f"SELECT SUM({sg}_pnl) p, SUM({sg}_cost) k FROM mx_lines WHERE {where}{lim} "
                                   f"AND {sg}_pnl IS NOT NULL AND (({sg}_labels & {bit}) != 0) = ? GROUP BY game_id",
                                   (*args, muted)).fetchall()
                    roi, roi_se = _ratio_se([(g["p"], g["k"]) for g in pg])
                    picks["muted" if muted else "shown"] = {"n": pr["n"], "won": pr["won"], "roi": roi, "se": roi_se}
                blk["picks_ev"] = picks
                hp = 1 << _LABEL_BITS.index("high_probability")
                # "said": the SHOWN figure the label was awarded on (the tuned one once a Platt is live)
                shown = f"COALESCE({sg}_pt, {sg}_p)"
                h = c.execute(f"SELECT COUNT(*) n, AVG(CASE WHEN COALESCE({sg}_side,'yes')='yes' THEN {shown} "
                              f"ELSE 1-{shown} END) pred, AVG(CASE WHEN COALESCE({sg}_side,'yes')='yes' THEN outcome "
                              f"ELSE 1-outcome END) hit FROM mx_lines WHERE {where}{lim} AND ({sg}_labels & {hp}) != 0",
                              args).fetchone()
                blk["picks_hp"] = dict(h)
                s["first" if sg == "f" else "close"] = blk
            cal = c.execute(f"SELECT CAST(MIN(c_p * 10, 9) AS INTEGER) b, COUNT(*) n, AVG(c_p) p, AVG(outcome) hit "
                            f"FROM mx_lines WHERE {where} AND game_ts - c_ts <= {CLOSE_WINDOW_S} GROUP BY b ORDER BY b",
                            args).fetchall()
            s["calibration"] = [dict(r) for r in cal]
            vr = c.execute(f"SELECT SUM(graded=3) v, COUNT(*) n, (f_labels & 7) != 0 lab FROM mx_lines WHERE sport=? "
                           f"AND graded IN (1,3){'' if stat == '*' else ' AND stat=?'} GROUP BY lab", args).fetchall()
            s["void_rate"] = {("labelled" if r["lab"] else "unlabelled"): (r["v"] or 0) / r["n"] for r in vr if r["n"]}
            out["stats"][stat] = s
    try:
        import calibrate
        out["tuning"] = ((calibrate.file_params() or {}).get("mx") or {}).get(sport) or {}
    except Exception as e:
        errlog.note("MXR-record-tuning", e)
        out["tuning"] = {}
    return out


def _ratio_se(groups):
    """ROI = sum pnl / sum cost with a game-clustered (delta-method) SE."""
    P = sum((g[0] or 0.0) for g in groups)
    K = sum((g[1] or 0.0) for g in groups)
    if K <= 0:
        return None, None
    r = P / K
    if len(groups) < 2:
        return r, None
    var = sum(((g[0] or 0.0) - r * (g[1] or 0.0)) ** 2 for g in groups)
    return r, math.sqrt(var) / K


# ---- tuning ------------------------------------------------------------------------
def _sig(z):
    return 1.0 / (1.0 + math.exp(-z)) if z >= 0 else math.exp(z) / (1.0 + math.exp(z))


def _lg(p):
    p = min(1.0 - 1e-6, max(1e-6, p))
    return math.log(p / (1.0 - p))


def _agg(rows):
    """(w, y, x1, x2) rows with equal (y, x1, x2) summed into one: the same
    likelihood exactly. The engines round p to four places, so a season of
    rungs collapses to at most ~2 x 10^4 rows for the engine-only fit; the
    server has no numpy and this runs in pure Python."""
    acc = {}
    for w, y, x1, x2 in rows:
        key = (y, x1, x2)
        acc[key] = acc.get(key, 0.0) + w
    return [(w, y, x1, x2) for (y, x1, x2), w in acc.items()]


def _fit_logit(rows, k):
    """Weighted logistic fit of y on (1, x1[, x2]) by Newton's method; rows are
    (w, y, x1, x2). Starts at the identity (0, 1[, 0]). Returns the params."""
    rows = _agg(rows)
    beta = [0.0, 1.0, 0.0][:k]
    for _it in range(30):
        g = [0.0] * k
        H = [[0.0] * k for _ in range(k)]
        for w, y, x1, x2 in rows:
            xs = (1.0, x1, x2)[:k]
            z = sum(b * x for b, x in zip(beta, xs))
            p = _sig(max(-30.0, min(30.0, z)))
            for i in range(k):
                g[i] += w * (y - p) * xs[i]
                for j in range(k):
                    H[i][j] += w * p * (1.0 - p) * xs[i] * xs[j]
        for i in range(k):
            H[i][i] += 1e-6 * sum(r[0] for r in rows) + 1e-9      # a whisper of ridge: never a singular step
        step = _solve(H, g)
        if step is None:
            break
        beta = [b + s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < 1e-7:
            break
    return beta


def _solve(A, b):
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for i in range(n):
        piv = max(range(i, n), key=lambda r: abs(M[r][i]))
        if abs(M[piv][i]) < 1e-15:
            return None
        M[i], M[piv] = M[piv], M[i]
        for r in range(n):
            if r != i:
                f = M[r][i] / M[i][i]
                for cc in range(i, n + 1):
                    M[r][cc] -= f * M[i][cc]
    return [M[i][n] / M[i][i] for i in range(n)]


def _wll(beta, w, y, x1, x2):
    z = beta[0] + beta[1] * x1 + (beta[2] * x2 if len(beta) > 2 else 0.0)
    return w * _ll(_sig(max(-30.0, min(30.0, z))), y)


def _forward(rows, k, base_k=None):
    """Out-of-sample gain of a k-parameter fit over the base (identity when
    base_k is None, else a base_k-parameter fit), by forward chaining over
    blocks of dates. rows: (day, game, w, y, x1, x2). Returns (gain a weighted
    row, se clustered by game, scored weighted rows)."""
    days = sorted({r[0] for r in rows})
    if len(days) <= FLOOR_DAYS:
        return None, None, 0.0
    rest = days[FLOOR_DAYS:]
    nb = min(8, len(rest))
    blocks = [rest[i * len(rest) // nb:(i + 1) * len(rest) // nb] for i in range(nb)]
    per_game = {}
    for blk in blocks:
        if not blk:
            continue
        lo = blk[0]
        tr = [(r[2], r[3], r[4], r[5]) for r in rows if r[0] < lo]
        te = [r for r in rows if r[0] in set(blk)]
        if not tr or not te:
            continue
        cand = _fit_logit(tr, k)
        base = _fit_logit(tr, base_k) if base_k else [0.0, 1.0]
        for d, gm, w, y, x1, x2 in te:
            diff = _wll(base, w, y, x1, x2) - _wll(cand, w, y, x1, x2)
            acc = per_game.setdefault(gm, [0.0, 0.0])
            acc[0] += diff
            acc[1] += w
    groups = list(per_game.values())
    W = sum(g[1] for g in groups)
    if W <= 0 or len(groups) < MIN_SCORED_GAMES:
        # the first blocks past the floor are a day or two of games: measured on a calibrated engine
        # (the build review), 2 SE over 4 games adopted falsely 6.5% of looks, one look every 6 h
        return None, None, W
    mean = sum(g[0] for g in groups) / W
    var = sum((g[0] - mean * g[1]) ** 2 for g in groups)
    return mean, (math.sqrt(var) / W if len(groups) > 1 else None), W


def _rows_for(c, sport, stat):
    """(day, game, w, y, logit p, logit mkt|None) for graded close sightings,
    each rung weighted 1/(rungs of its player-stat in this set)."""
    raw = c.execute(f"SELECT game_id, {_WHO} player, c_p, c_mkt, outcome, {_DAY} d FROM mx_lines "
                    "WHERE sport=? AND stat=? AND graded=1 AND outcome IN (0,1) AND c_p > 0 AND c_p < 1 "
                    f"AND game_ts - c_ts <= {CLOSE_WINDOW_S}", (sport, stat)).fetchall()
    rungs = {}
    for r in raw:
        rungs[(r["game_id"], r["player"])] = rungs.get((r["game_id"], r["player"]), 0) + 1
    out = []
    for r in raw:
        w = 1.0 / rungs[(r["game_id"], r["player"])]
        out.append((r["d"], r["game_id"], w, int(r["outcome"]), _lg(r["c_p"]),
                    _lg(r["c_mkt"]) if r["c_mkt"] is not None else None))
    return out


def fit_stat(rows, prev=None):
    """The tuning for one sport x stat from its rows (see _rows_for). `prev`:
    this stat's tuning in the last file. The decision is STICKY (the build
    review's MAJOR 1): a stat already tuned keeps its Platt while the gain stays
    above 0 and 1 SE; one on the identity needs MIN_GAIN and 2 SE. A gate
    looked at every 6 h all season would otherwise flip on noise."""
    days = len({r[0] for r in rows})
    neff = sum(r[2] for r in rows)
    res = {"family": "identity", "a": 0.0, "b": 1.0, "n": len(rows), "n_eff": round(neff, 1), "days": days,
           "gain": None, "se": None, "fitted_ts": int(time.time())}
    if neff < FLOOR_NEFF or days <= FLOOR_DAYS:
        # the chain scores only the dates past FLOOR_DAYS: at FLOOR_DAYS there is nothing to score
        res["why"] = "below the floors"
        return res
    pr = [(r[0], r[1], r[2], r[3], r[4], 0.0) for r in rows]
    gain, se, _w = _forward(pr, 2)
    res.update(gain=gain, se=se)
    was = isinstance(prev, dict) and prev.get("family") == "platt"
    if gain is None or se is None:
        res["why"] = (f"nothing scored out of sample yet (it needs {MIN_SCORED_GAMES} games past the first "
                      f"{FLOOR_DAYS} dates)")
    elif (gain > MIN_GAIN and gain > 2.0 * se) or (was and gain > 0.0 and gain > se):
        a, b = _fit_logit([(r[2], r[3], r[4], 0.0) for r in rows], 2)
        if b > 0.05:                       # monotone, so a band stays a band
            lam = min(1.0, days / DAMP_DAYS)
            res.update(family="platt", a=round(a * lam, 4), b=round(1.0 + (b - 1.0) * lam, 4), damp=round(lam, 3),
                       kept=bool(was and not (gain > MIN_GAIN and gain > 2.0 * se)))
        else:
            res["why"] = "fit was not increasing in the engine's figure"
    else:
        res["why"] = "did not beat the identity out of sample" + (" (the tuning it had fell under 1 SE)" if was else "")
    # the blend, REPORTED only: on rows with a believable book, against the engine-only fit
    br = [r for r in rows if r[5] is not None]
    if sum(r[2] for r in br) >= FLOOR_NEFF and len({r[0] for r in br}) >= FLOOR_DAYS:
        bg, bse, _bw = _forward(br, 3, base_k=2)
        res["blend_report"] = {"gain_over_platt": bg, "se": bse, "n_eff": round(sum(r[2] for r in br), 1),
                               "applied": False}
    return res


def verdict(c, sport, stat):
    """Engine vs Kalshi on the live close record for one stat: decided only past
    the floors and by 2 SE (clustered by game)."""
    rows = c.execute("SELECT game_id, SUM(c_ll_eng - c_ll_mkt) d, COUNT(*) n, SUM(c_ll_eng) e, SUM(c_ll_mkt) m "
                     "FROM mx_lines WHERE sport=? AND stat=? AND graded=1 "
                     f"AND c_ll_mkt IS NOT NULL AND game_ts - c_ts <= {CLOSE_WINDOW_S} GROUP BY game_id",
                     (sport, stat)).fetchall()
    n = sum(r["n"] for r in rows)
    days = c.execute(f"SELECT COUNT(DISTINCT {_DAY}) FROM mx_lines WHERE sport=? AND stat=? "
                     f"AND graded=1 AND c_ll_mkt IS NOT NULL AND game_ts - c_ts <= {CLOSE_WINDOW_S}",
                     (sport, stat)).fetchone()[0]
    d, se = _se_paired([(r["d"], r["n"]) for r in rows])
    out = {"n": n, "games": len(rows), "days": days, "diff": d, "se": se,
           "decided": _decide(n, len(rows), days, d, se)}
    if out["decided"]:
        out.update(model_ll=sum(r["e"] for r in rows) / n, market_ll=sum(r["m"] for r in rows) / n)
    return out


def fit_all(prev=None):
    """{sport: {stat: tuning}, "verdict": {sport: {stat: verdict}}} -- run by
    calibrate.compute_params inside the background owner's niced child. `prev`:
    the last file's block, so each stat's decision is sticky (fit_stat)."""
    init_db()
    out = {"verdict": {}, "fitted_ts": int(time.time())}
    with _lock, _conn() as c:
        for sport in SPORTS:
            stats = [r[0] for r in c.execute("SELECT DISTINCT stat FROM mx_lines WHERE sport=? AND graded=1",
                                             (sport,)).fetchall() if r[0]]
            out[sport] = {}
            out["verdict"][sport] = {}
            for stat in stats:
                try:
                    out[sport][stat] = fit_stat(_rows_for(c, sport, stat),
                                                ((prev or {}).get(sport) or {}).get(stat))
                    out["verdict"][sport][stat] = verdict(c, sport, stat)
                except Exception as e:
                    errlog.note("MXR-fit", e, path=f"{sport} {stat}")
    return out


# ---- applying the tuning (build time, server child and PC alike) --------------------
def tuning_for(sport):
    try:
        import calibrate
        return ((calibrate.file_params() or {}).get("mx") or {}).get(sport) or {}
    except Exception as e:
        errlog.note("MXR-tuning-read", e)
        return {}


def apply_tuning(payload, sport, tun=None, now=None):
    """Map every line's probability through its stat's adopted Platt (p_raw keeps
    the engine's), recompute its value and labels with the engine's own
    helpers, and stamp the payload. In place; returns lines retuned."""
    tun = tuning_for(sport) if tun is None else tun
    live = {st: t for st, t in (tun or {}).items() if isinstance(t, dict) and t.get("family") == "platt"
            and (t.get("b") or 0) > 0}
    payload["tuning"] = {st: {k: t.get(k) for k in ("family", "a", "b", "n_eff", "days", "gain", "se", "fitted_ts")}
                         for st, t in (tun or {}).items() if isinstance(t, dict)}
    if not live:
        return 0
    import prop_value as PV
    now = time.time() if now is None else now
    n = 0
    for pl in payload.get("players") or []:
        for m in pl.get("markets") or []:
            t = live.get(m.get("stat"))
            if not t:
                continue
            a, b = float(t["a"]), float(t["b"])
            # four places, as both engines round theirs (monotone, so lo <= p <= hi survives it)
            f = lambda p: round(_sig(a + b * _lg(p)), 4)             # noqa: E731
            rev = f"platt:{t.get('fitted_ts')}"
            for ln in m.get("lines") or []:
                p = _num(ln.get("p"))
                if p is None:
                    continue
                lo, hi = _num(ln.get("p_lo")), _num(ln.get("p_hi"))
                q = f(p)
                ql = None if lo is None or hi is None else min(q, f(lo))
                qh = None if lo is None or hi is None else max(q, f(hi))
                ln["p_raw"], ln["p"], ln["tuning"] = p, q, rev
                if ql is not None:
                    ln["p_lo"], ln["p_hi"] = ql, qh
                try:
                    _relabel(ln, sport, q, ql, qh, PV, now)
                except Exception as e:
                    errlog.note("MXR-relabel", e, path=str((ln.get("market") or {}).get("ticker")))
                n += 1
    return n


def _relabel(ln, sport, p, lo, hi, PV, now):
    """The engine's own value and labels for the retuned probability."""
    mk = ln.get("market")
    n_eff = (ln.get("hit_similar_def") or {}).get("n_eff")
    side = ln.get("fit_side")
    if sport == "nba":
        import nba_matchup
        val = PV.line_value(p, mk, lo, hi, now=now) if mk else None
        ln["value"] = val
        ln["labels"] = nba_matchup._labels(p, lo if lo is not None else p, hi if hi is not None else p, n_eff, side, val)
    else:
        import nfl_matchup
        shift = nfl_matchup.STRONG_FIT if side == "yes" else (-nfl_matchup.STRONG_FIT if side == "no" else 0.0)
        val, labels, _fs = nfl_matchup.line_labels(p, lo, hi, mk, shift, n_eff, now=now)
        ln["value"], ln["labels"] = val, labels


def market_verdicts(sport):
    """{stat: verdict} from the shared params (fit_all's), for mark_record."""
    try:
        import calibrate
        return (((calibrate.file_params() or {}).get("mx") or {}).get("verdict") or {}).get(sport) or {}
    except Exception as e:
        errlog.note("MXR-verdict-read", e)
        return {}


# ---- the loop (background owner) -------------------------------------------------------
_started = [False]
HARVEST_S = 600                 # one pass: harvest, grade, and refit when due


def tick(now=None):
    """One pass: harvest both sports' boards, grade what has finished, and refit
    the shared calibration file when due (calibrate.write_params, in a niced
    child under the heavy-build gate)."""
    n = harvest_all(now)
    g = grade_due(now)
    try:
        import calibrate
        if calibrate.params_due():
            import deep_cache
            if deep_cache.HEAVY_BUILD.acquire(timeout=60):
                try:
                    deep_cache.run_child("calibrate", "write_params", timeout=1800)
                finally:
                    deep_cache.HEAVY_BUILD.release()
    except Exception as e:
        errlog.note("MXR-params", e)
    return n, g


def start():
    """The background owner's record loop; once per process, never in a test."""
    if _started[0] or os.environ.get("VIGIL_NO_BG"):
        return
    _started[0] = True

    def _loop():
        time.sleep(30)
        while True:
            try:
                tick()
            except Exception as e:
                errlog.note("MXR-loop", e)
            time.sleep(HARVEST_S)
    threading.Thread(target=_loop, daemon=True, name="mx-record").start()
