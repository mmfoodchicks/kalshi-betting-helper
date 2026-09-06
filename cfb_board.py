"""College football week board and combo maker -- the NFL slate's twin.

  DATA   ESPN's college scoreboard for the week (both-FBS games only: the
         ratings cover FBS, and an FCS visitor has none), the in-season
         Massey ratings from cfb.inseason_ratings (backtested 71.2% winner
         accuracy by November; week 1 is the regressed prior), and Kalshi's
         college game markets through kalshi_cfb.
  SIM    Every game is played DRIVE BY DRIVE through the shared football
         engine (nfl_game_sim._play_game at college scoring rates, cfb's own
         _rates_for) -- N simulated games give the win probability, the
         expected score, spread and total ladders and the bitmask candidate
         legs the combo engine prices same-game stacks with. No player
         board: nothing projects college players game by game, and a
         pick'em needs the game, not the yardage.
  PICK'EM Every card carries the straight-up pick with its probability and a
         confidence RANK across the week (the surest pick ranks 1), plus the
         against-the-spread pick at the rung Kalshi books nearest its own
         line, and the total lean at Kalshi's total: P(cover) and P(over)
         are read off the simulated margins and totals, never off the
         moneyline.
  RECORD cfb_track logs the pre-game pick and grades it off the final, in
         the same ledger as the NFL picks under league "cfb"; the ATS and
         total picks file as Kalshi tickets (leagues cfb_ats / cfb_tot) and
         grade on Kalshi's own settlement.

Board and sims are published through boardshare, so the PC builds them on
its own cores and the server adopts (pc_worker._task_boards); the server
self-computes only when the PC is off.
"""

import datetime
import math as _math
import random
import threading
import time as _time
import zlib
import zoneinfo

import errlog
import clock
import predlog
import cfb
import kalshi_cfb

_N = 2000
_BOARD_TTL = 1800
# Market anchor. The ratings are a heavily regressed prior in September (the
# season sim's measured design: 57.6% -> 71.2% winner accuracy comes from the
# in-season learning, not the prior), so the LEVEL of a priced game is blended
# toward Kalshi's own line -- the spread ladder's de-vigged midpoint, or the
# moneyline turned into a margin through the engine's margin SD -- by the
# share the college model has EARNED on graded results (model_trust "cfb"),
# market-heavy until it has. Every card shows the raw model margin beside the
# line, so the blend is visible rather than silent. Unpriced games run on the
# ratings alone.
_ANCHOR_W_DEFAULT = 0.25
_MARGIN_SD = 18.0          # measured 2026-09-05: pstdev of 3,000 simulated
                           # margins at a +6 rating gap = 18.1
_SIMS_TTL = 1800
_LAST_WEEK = 16
_ET = zoneinfo.ZoneInfo("America/New_York")
_cache = {}
_inflight = set()


def _season():
    return cfb._season()


# ---- The week's games (ESPN scoreboard) ------------------------------------
def _week_games(season, week):
    """[{id, date, state, neutral, home, away}] for one week, both sides RATED
    (FBS or FCS -- cfb.teams(season, "all")). home/away carry ESPN's id,
    abbreviation, display name, location and nickname (the last three are what
    the Kalshi matcher scores), the division, and the score once the game has
    one.

    Both divisions' scoreboards are swept: ESPN files a game under the
    division of its teams, so an FCS game is only on `groups=81` and an
    FBS-vs-FCS buy game appears on both (deduped by event id below)."""
    import racing

    def build():
        events, seen_ids = [], set()
        for grp in (cfb._FBS_GROUP, cfb._FCS_GROUP):
            try:
                d = racing._get_json(
                    f"{cfb._SITE}/scoreboard?seasontype=2&week={week}&dates={season}"
                    f"&groups={grp}&limit=400", timeout=25)
            except Exception as _e:
                errlog.note("CFB-week-group", _e, path=str(grp))
                continue
            for e in d.get("events", []):
                if e.get("id") in seen_ids:
                    continue      # a cross-division game is on both boards
                seen_ids.add(e.get("id"))
                events.append(e)
        rated = cfb.teams(season, "all")
        fbs = set(rated)
        out = []
        for e in events:
            comp = (e.get("competitions") or [{}])[0]
            stype = ((comp.get("status") or {}).get("type")) or {}
            state = stype.get("state")
            # postponed / canceled: "post" with completed false and 0-0 (see
            # cfb.schedule) -- never a card, never a graded pick
            if state == "post" and not stype.get("completed"):
                continue
            sides = {}
            for c in comp.get("competitors", []):
                tm = c.get("team") or {}
                try:
                    sc = float(c.get("score"))
                except (TypeError, ValueError):
                    sc = None
                tid = str(tm.get("id") or c.get("id"))
                sides[c.get("homeAway")] = {
                    "id": tid,
                    "abbr": tm.get("abbreviation"), "name": tm.get("displayName"),
                    "location": tm.get("location"), "nick": tm.get("name"),
                    "div": (rated.get(tid) or {}).get("div"),
                    "score": sc}
            h, a = sides.get("home"), sides.get("away")
            if not h or not a or h["id"] not in fbs or a["id"] not in fbs:
                continue
            out.append({"id": e.get("id"), "date": e.get("date"), "state": state,
                        "neutral": bool(comp.get("neutralSite")),
                        "home": h, "away": a})
        return out
    return racing._cached(("cfb_week", season, week, 1), 1800, build) or []


def _labor_day(year):
    """First Monday of September, the college calendar's anchor."""
    d = datetime.date(year, 9, 1)
    return d + datetime.timedelta(days=(7 - d.weekday()) % 7)


def current_week(today=None):
    """The week the College tab opens on, from the calendar alone.

    ESPN numbers college weeks Tuesday through Monday with week 1 ending on
    Labor Day and the "Week 0" Saturday folded into it (2025: week 1 ran
    Aug 23 - Sep 1 and week 2 opened Sep 2; 2026: Aug 29 - Sep 7, which is
    the week-1 feed this board was smoked against). The first cut asked the
    feed instead, one scoreboard fetch per week until a week with games
    left, and on the server that cost up to sixteen refused calls per page
    load: ESPN's WAF answers this host 403 in bursts, every week read as
    "failed", and the tab opened on week 16 with "no games" while the PC's
    week-1 board sat in the store (2026-09-05, mid-slate). The calendar
    needs no network; the week select covers the rest."""
    today = today or clock.today_et()
    season = today.year if today.month >= 2 else today.year - 1   # cfb's rule
    ld = _labor_day(season)
    if today <= ld:
        return 1
    return max(1, min(_LAST_WEEK, 1 + ((today - ld).days + 6) // 7))


def _et_date(iso):
    try:
        return datetime.datetime.fromisoformat(
            (iso or "").replace("Z", "+00:00")).astimezone(_ET).date()
    except (ValueError, TypeError):
        return None


def _dates_for(iso):
    """The Kalshi date keys a kickoff may be listed under: its ET date and
    the day either side (a late game crosses the calendar)."""
    d = _et_date(iso)
    if not d:
        return set()
    return {kalshi_cfb.date_key(d + datetime.timedelta(days=o)) for o in (-1, 0, 1)}


def _iso_ts(s):
    import kalshi
    try:
        return kalshi._parse_time(s) if s else None
    except (ValueError, TypeError):
        return None


# ---- One game through the drive engine --------------------------------------
def simulate_game(home, away, r_home, r_away, neutral=False, n=_N, seed=None,
                  ladders=None):
    """Correlated Monte Carlo of one game from two ratings (margin points a
    game, cfb.inseason_ratings). `home`/`away` are {"abbr", "name"} where
    abbr is the code the candidate legs' krefs price under (Kalshi's when
    the game is matched). Returns win prob, expected score, ladders and the
    bitmask candidate legs; `_margins` rides along for the cover math."""
    import nfl_game_sim
    # crc32, not hash(): str hashing is salted per process, so the "same"
    # seed gave every worker, the PC and each guard run a different slate
    # (the maker guard passed in one suite and returned no slip in the next).
    rng = random.Random(seed if seed is not None
                        else zlib.crc32(f"{away['abbr']}@{home['abbr']}".encode()))
    rh, ra = cfb._rates_for(r_home, r_away, 0.0 if neutral else 1.0)
    margins, totals = [], []
    pts_h = pts_a = 0.0
    hw = 0
    for _ in range(n):
        ph, pa = cfb._play(rh, ra, rng)
        if ph > pa:
            hw += 1
        margins.append(ph - pa)
        totals.append(ph + pa)
        pts_h += ph
        pts_a += pa
    p_home = hw / n
    mean_total = sum(totals) / n
    total_ladder = []
    base = round(mean_total)
    for line in [base + d + 0.5 for d in range(-8, 9)]:
        over = sum(1 for t in totals if t > line) / n
        if 0.04 <= over <= 0.96:
            total_ladder.append({"line": line, "over_pct": round(over * 100, 1),
                                 "under_pct": round((1 - over) * 100, 1)})
    spread_ladder = {"home": {}, "away": {}}
    for m in (1, 3, 4, 7, 10, 14, 17, 21, 28):
        spread_ladder["home"][str(m)] = round(100 * sum(1 for x in margins if x >= m) / n, 1)
        spread_ladder["away"][str(m)] = round(100 * sum(1 for x in margins if x <= -m) / n, 1)
    masks = nfl_game_sim._build_masks(home, away, [], [], [], margins, totals,
                                      p_home, n, ladders=ladders)
    return {"p_home": round(p_home, 4), "p_away": round(1 - p_home, 4),
            "exp_home": round(pts_h / n, 1), "exp_away": round(pts_a / n, 1),
            "exp_total": round(mean_total, 1),
            "mean_margin": round(sum(margins) / n, 1),
            "total_ladder": total_ladder, "spread_ladder": spread_ladder,
            "n_sims": n, "_masks": masks, "_margins": margins, "_totals": totals}


def _model_weight():
    """How much of a priced game's level the ratings keep, 0-1."""
    try:
        import model_trust
        if (model_trust.load().get("weights") or {}).get("cfb"):
            return max(0.0, min(1.0, model_trust.weight("cfb")))
    except Exception as _e:
        errlog.note("CFB-model-weight", _e)
    return _ANCHOR_W_DEFAULT


def _norm_ppf(p):
    """Inverse normal CDF (Acklam's approximation), for a moneyline-only
    market margin."""
    import math
    p = max(1e-6, min(1 - 1e-6, p))
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    if p < 0.02425:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > 1 - 0.02425:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def _market_margin(idx, suffix, hc, ac):
    """The market's expected home margin: the spread ladder's midpoint when
    booked, else the de-vigged moneyline through the margin SD; (margin,
    source) or (None, None)."""
    line = kalshi_cfb.implied_margin(idx, suffix, hc)
    if line is not None:
        return line, "spread"
    px = kalshi_cfb.game_prices(idx, suffix, hc, ac) or {}
    h, a = px.get("home_cents"), px.get("away_cents")
    if h is None or a is None or h + a <= 0:
        return None, None
    p = h / (h + a)
    return round(_MARGIN_SD * _norm_ppf(p), 1), "moneyline"


def _cover_pct(margins, line):
    """P(home margin beats `line`) off the simulated margins; a push (a whole
    number line landed on exactly) counts as neither."""
    n = len(margins) or 1
    return round(100.0 * sum(1 for m in margins if m > line) / n, 1)


# ---- The board ----------------------------------------------------------------
def _build_board(season, week, n=_N):
    games = _week_games(season, week)
    if not games:
        return None
    R = cfb.inseason_ratings(season, "all")
    meta = cfb.teams(season, "all")
    played = sum(1 for g in cfb.schedule(season, "all")
                 if g.get("final") and g.get("margin") is not None
                 and g.get("home") and g.get("away"))
    try:
        idx = kalshi_cfb.index() or {}
    except Exception as _e:
        errlog.note("CFB-board-index", _e)
        idx = {}
    try:
        import calibrate
        cal = lambda p: max(0.03, min(0.97, calibrate.apply("cfb", p)))
    except Exception:
        cal = lambda p: p
    w_model = _model_weight()
    out, sims, log_rows = [], [], []
    ats_rows, tot_rows = [], []
    for g in games:
        h, a = g["home"], g["away"]
        rh, ra = R.get(h["id"], 0.0), R.get(a["id"], 0.0)
        m = kalshi_cfb.match_game(idx, _dates_for(g["date"]), h, a) if idx else None
        suffix, hc, ac = m if m else (None, h["abbr"], a["abbr"])
        lad = kalshi_cfb.ladders(idx, suffix) if suffix else None
        host = 0.0 if g["neutral"] else 1.0
        model_margin = (rh - ra) + cfb._HFA_MARGIN * host
        mkt_margin, mkt_src = (_market_margin(idx, suffix, hc, ac)
                               if suffix else (None, None))
        if mkt_margin is not None:
            fair_margin = w_model * model_margin + (1 - w_model) * mkt_margin
            shift = fair_margin - model_margin
            rh_s, ra_s = rh + shift / 2.0, ra - shift / 2.0
        else:
            fair_margin, rh_s, ra_s = model_margin, rh, ra
        sim = simulate_game({"abbr": hc, "name": h["name"]},
                            {"abbr": ac, "name": a["name"]},
                            rh_s, ra_s, neutral=g["neutral"], n=n, ladders=lad)
        # The RAW model's number, for the calibrator and the record: the
        # ratings' margin through the engine's margin SD, no market in it.
        raw = 0.5 * (1 + _math.erf(model_margin / (_MARGIN_SD * _math.sqrt(2))))
        ph = cal(sim["p_home"]) if mkt_margin is None else sim["p_home"]
        dh, da = h.get("div") or "fbs", a.get("div") or "fbs"
        card = {"home": h["abbr"], "away": a["abbr"],
                "home_name": h["name"], "away_name": a["name"],
                "home_id": h["id"], "away_id": a["id"],
                "div_home": dh, "div_away": da,
                # what the record files this game under: one book per division,
                # plus cross for a buy game, so an FCS pick can never flatter
                # the FBS scoreboard
                "division": dh if dh == da else "cross",
                "date": g["date"], "state": g["state"], "neutral": g["neutral"],
                "conf_home": (meta.get(h["id"]) or {}).get("conf"),
                "conf_away": (meta.get(a["id"]) or {}).get("conf"),
                "rating_home": round(rh, 1), "rating_away": round(ra, 1),
                "model_margin": round(model_margin, 1),
                "fair_margin": round(fair_margin, 1),
                "market_src": mkt_src, "model_weight": round(w_model, 2),
                "p_home": round(ph, 4), "p_away": round(1 - ph, 4),
                "p_home_raw": round(raw, 4),
                "exp_home": sim["exp_home"], "exp_away": sim["exp_away"],
                "exp_total": sim["exp_total"], "mean_margin": sim["mean_margin"],
                "spread_ladder": sim["spread_ladder"],
                "total_ladder": ([r for r in sim["total_ladder"]
                                  if 20 <= r["over_pct"] <= 80][:5]
                                 or sim["total_ladder"][:3]),
                "n_sims": n, "players": [], "props": [], "sgp": None,
                "home_score": h.get("score"), "away_score": a.get("score"),
                "kx": ({"suffix": suffix, "home": hc, "away": ac} if suffix else None)}
        if suffix:
            px = kalshi_cfb.game_prices(idx, suffix, hc, ac) or {}
            card["kalshi"] = {"home_cents": px.get("home_cents"),
                              "away_cents": px.get("away_cents")}
            if px.get("home_cents") is not None:
                card["edge_home"] = round(ph * 100 - px["home_cents"], 1)
            if px.get("away_cents") is not None:
                card["edge_away"] = round((1 - ph) * 100 - px["away_cents"], 1)
            pre = (g.get("state") or "") == "pre"
            card["market_margin"] = mkt_margin
            rung = (kalshi_cfb.spread_rung(idx, suffix, hc, ac, mkt_margin)
                    if mkt_src == "spread" else None)
            if rung:
                # Against the spread at the rung Kalshi actually books nearest
                # its line, so the pick is a ticket (the record grades it on
                # Kalshi's own settlement, cfb_track) rather than a number
                # between two rungs. Who covers, and how often the simulated
                # margins say so; the rung's YES side is "team wins by >= by",
                # which is the home cover on the home ladder and the away
                # cover on the away one.
                pc = _cover_pct(sim["_margins"], rung["line"])
                home_covers = pc >= 50.0
                yes_home = rung["team"] == hc
                p_yes = pc / 100.0 if yes_home else 1.0 - pc / 100.0
                pick_yes = home_covers == yes_home
                # the pick's own number: the home line negated for the home
                # side (IND -17.5), as is for the visitor (UNT +17.5)
                own = -rung["line"] if home_covers else rung["line"]
                card["ats"] = {"line": rung["line"], "by": rung["by"], "kx_team": rung["team"],
                               "team": h["abbr"] if home_covers else a["abbr"],
                               "name": h["name"] if home_covers else a["name"],
                               "pct": pc if home_covers else round(100.0 - pc, 1),
                               "spread": f"{own:+.1f}", "side": "yes" if pick_yes else "no",
                               "mkt_pct": round(100.0 * (rung["mkt"] if pick_yes
                                                         else 1.0 - rung["mkt"]), 1),
                               "ask": rung["ask"] if pick_yes else rung["no_ask"],
                               "ticker": rung["ticker"]}
                if pre and rung["ticker"]:
                    ats_rows.append((rung["ticker"], min(0.999, max(0.001, p_yes)),
                                     rung["close"], rung["mkt"], _iso_ts(g.get("date"))))
            tot = kalshi_cfb.total_rung(idx, suffix)
            if tot:
                # The total at Kalshi's own line: how often the simulated
                # totals clear it, and the lean that follows.
                n_t = len(sim["_totals"]) or 1
                over = sum(1 for t in sim["_totals"] if t > tot["line"]) / n_t
                lean_over = over >= 0.5
                card["total"] = {"line": tot["line"], "over_pct": round(100.0 * over, 1),
                                 "lean": "over" if lean_over else "under",
                                 "pct": round(100.0 * (over if lean_over else 1.0 - over), 1),
                                 "mkt_pct": round(100.0 * (tot["mkt"] if lean_over
                                                           else 1.0 - tot["mkt"]), 1),
                                 "side": "yes" if lean_over else "no",
                                 "ask": tot["ask"] if lean_over else tot["no_ask"],
                                 "ticker": tot["ticker"]}
                if pre and tot["ticker"]:
                    tot_rows.append((tot["ticker"], min(0.999, max(0.001, over)),
                                     tot["close"], tot["mkt"], _iso_ts(g.get("date"))))
            if pre:
                for tk, p, own, opp in (
                        (px.get("home_ticker"), raw, px.get("home_cents"), px.get("away_cents")),
                        (px.get("away_ticker"), 1 - raw, px.get("away_cents"), px.get("home_cents"))):
                    if tk:
                        log_rows.append((tk, p, px.get("close"), predlog.devig(own, opp),
                                         _iso_ts(g.get("date"))))
        pick_home = ph >= 0.5
        card["pick"] = {"team": h["abbr"] if pick_home else a["abbr"],
                        "name": h["name"] if pick_home else a["name"],
                        "pct": round((ph if pick_home else 1 - ph) * 100, 1)}
        out.append(card)
        sims.append({"label": f"{a['name']} @ {h['name']}", "suffix": suffix,
                     "pair": f"{a['abbr']}@{h['abbr']}", "cands": sim["_masks"],
                     "n": n, "date": g["date"], "state": g["state"],
                     "division": card["division"],
                     "home": h["abbr"], "away": a["abbr"], "kx_home": hc, "kx_away": ac})
    if log_rows:
        try:
            predlog.init_db()
            predlog.log_many("cfb", log_rows)
        except Exception as _e:
            errlog.note("CFB-build_board", _e)
    # The ATS and total forecasts file under their own models: they are
    # coin-flip-shaped numbers at a line, and mixing them into the moneyline
    # bucket would bend the calibrator that bucket fits (calibrate "cfb").
    for model, rows in (("cfb_ats", ats_rows), ("cfb_total", tot_rows)):
        if rows:
            try:
                predlog.init_db()
                predlog.log_many(model, rows)
            except Exception as _e:
                errlog.note("CFB-build_board-lines", _e)
    out.sort(key=lambda g: g["date"] or "")
    # Pick'em confidence: rank the straight-up picks by probability, surest
    # first. `confidence` is the points a standard confidence pool assigns
    # (n games for the surest pick, 1 for the coin flip).
    order = sorted(range(len(out)), key=lambda i: -out[i]["pick"]["pct"])
    for rank, i in enumerate(order, 1):
        out[i]["pick"]["rank"] = rank
        out[i]["pick"]["confidence"] = len(out) - rank + 1
    import boardshare
    boardshare.put(f"cfb_parlay_sims_{season}_w{week}_{n}", sims)
    return {"season": season, "week": week, "engine": "drive", "n_games": len(out),
            "built_ts": _time.time(),
            "n_sims": n, "games": out, "games_played": played,
            "ratings": "in-season Massey" if played else "preseason prior",
            "model_weight": round(w_model, 2),
            "note": ("Drive-level Monte Carlo from in-season Massey ratings (last "
                     "season's margins as the prior, fading as this season is "
                     "played). A priced game's LEVEL is blended toward Kalshi's own "
                     "line by the share the model has earned on graded results; the "
                     "raw model margin shows beside it. The pick'em rank orders the "
                     "week's straight-up picks by how sure the blended sim is; the "
                     "ATS pick is read off the simulated margins at the rung Kalshi "
                     "books nearest its line, the total lean off the simulated "
                     "totals at Kalshi's total. FBS and FCS are both rated and both boards are swept, so conference games in either division and the cross-division buy games are all here; an FCS rating sits on the FBS scale through a fitted division offset and is a heavier regressed prior for longer, so treat an early FCS number as the model's opinion.")}


def _last_good(name):
    """The newest board on disk under `name` whatever its age, if it had
    games; (None, None) otherwise."""
    import boardshare
    old, age = boardshare.get(name, None)
    if old and old.get("games"):
        return old, age
    return None, None


def _stale(val, age, why):
    """A good board re-served past its TTL, saying so on the summary line.
    The pick'em, the cards and the maker keep working off it (the maker
    prices every leg live and drops anything that has kicked off); the note
    carries the true age (built_ts, else the file's) and why the rebuild
    did not replace it."""
    built = val.get("built_ts")
    age_s = int(_time.time() - built) if built else int(age or 0)
    out = dict(val)
    out["stale"] = True
    out["stale_s"] = age_s
    out["note"] = f"served from the last good build ({age_s // 60}m old): {why}"
    return out


def board(week=1):
    """Non-blocking weekly slate, published through boardshare so every
    worker (and the PC) serves one build. nfl_game_sim.board's pattern, with
    one rule on top: a rebuild that fails or comes back empty never replaces
    a board that had games. The first Saturday (2026-09-05, PC off, ESPN
    refusing the server) the failure placeholder overwrote the PC's last
    upload and the tab read "could not be built" over a slate that was on
    disk a minute earlier. The placeholder's short age still throttles the
    retry to one attempt per two minutes across every worker."""
    import boardshare
    season = _season()
    key = ("cfb_slate", season, week)
    name = f"cfb_slate_{season}_w{week}"
    hit = _cache.get(key)
    if hit and _time.time() - hit[0] < _BOARD_TTL:
        return hit[1]
    disk, age = boardshare.get(name, _BOARD_TTL)
    if disk is not None:
        _cache[key] = (_time.time() - age, disk)
        return disk
    if key not in _inflight and boardshare.claim(name):
        _inflight.add(key)

        def _bg():
            try:
                import jobs
                with jobs.timed(f"cfb-board:w{week}"):
                    val = _build_board(season, week)
                if val is None:
                    old, oage = _last_good(name)
                    if old is not None:
                        # A feed listing no games for a week that had them
                        # is a feed glitch, not a schedule change.
                        val = _stale(old, oage, "the feed listed no games")
                    else:
                        val = {"season": season, "week": week, "games": [], "n_games": 0,
                               "empty": True,
                               "note": "No FBS games found for this week."}
                    _cache[key] = (_time.time() - 1500, val)
                    boardshare.put(name, val, age=1500)
                else:
                    _cache[key] = (_time.time(), val)
                    boardshare.put(name, val)
            except Exception as e:
                errlog.note("CFB-board-build", e, path=f"s{season} w{week}")
                old, oage = _last_good(name)
                if old is not None:
                    val = _stale(old, oage, f"the rebuild failed ({e})")
                else:
                    val = {"season": season, "week": week, "games": [], "n_games": 0,
                           "empty": True, "error": str(e),
                           "note": "The board could not be built; retrying shortly."}
                _cache[key] = (_time.time() - 1680, val)
                boardshare.put(name, val, age=1680)
            finally:
                _inflight.discard(key)
                boardshare.release(name)

        threading.Thread(target=_bg, daemon=True).start()
    if hit:
        return hit[1]
    # Nothing fresh anywhere: the last good build on disk, however old, beats
    # a spinner while the rebuild runs (or cannot run).
    old, oage = _last_good(name)
    return _stale(old, oage, "a rebuild is in flight") if old is not None else None


# ---- Combo maker --------------------------------------------------------------
def _slate_sims(week, n=_N):
    """The week's candidate legs with their sim masks, written by the board
    build. None while the board is still building (the caller says so)."""
    import boardshare
    name = f"cfb_parlay_sims_{_season()}_w{week}_{n}"
    sims, _age = boardshare.get(name, _SIMS_TTL)
    if sims is None:
        board(week)                    # kicks the build if nobody has
        # Past their TTL the sims still describe the unplayed games (the
        # maker drops anything that has kicked off and prices every leg
        # live), so the last set on disk beats "building" while ESPN
        # refuses the server a rebuild.
        sims, _age = boardshare.get(name, None)
    return sims


def price_cands(cands, suffix, idx, blend=True):
    """Live Kalshi ask + market-blended probability per candidate, in place."""
    import combo_engine
    quotes = {}
    for c in cands:
        px, q = None, None
        if idx and suffix:
            try:
                px = kalshi_cfb.price_leg(idx, suffix, c.get("kref"))
                q = kalshi_cfb.quote_leg(idx, suffix, c.get("kref"))
            except Exception:
                px, q = None, None
        c["price_cents"] = px
        quotes[id(c)] = q
    if blend:
        combo_engine.blend_candidates(cands, quotes, sport="cfb")
    return cands


def build_parlay(week=1, n_legs=3, target_pct=55, cap_pct=None, target_payout=0,
                 max_legs_per_game=3, max_total_legs=8, legs_mode="prefer",
                 payout_mode="off", conn="or", objective="balanced", types=None,
                 game_sel=None, max_bet=False, cap_x=None, abort_cb=None, div=None):
    """One parlay across the week's college games, priced against Kalshi --
    nfl_game_sim.build_parlay on the college slate, knob for knob.

    `div` scopes the pool to one division's tab ("fbs" or "fcs"): a slip
    built on the FCS board must not quietly take an FBS leg, and a buy game
    belongs to both. Without it the whole college slate is in play."""
    import combo_engine
    import mlb_sim
    floor = max(0.05, min(0.97, target_pct / 100.0))
    ceil = 1.0
    if cap_pct is not None and cap_pct / 100.0 > floor:
        ceil = min(1.0, cap_pct / 100.0)
    games = _slate_sims(week)
    if games is None:
        return {"error_hint": "building"}
    now = _time.time()

    def _started(g):
        if (g.get("state") or "").lower() in ("post", "in"):
            return True
        ts = _iso_ts(g.get("date"))
        return bool(ts) and ts + 300 < now

    if div in ("fbs", "fcs"):
        other = "fcs" if div == "fbs" else "fbs"
        games = [g for g in games if (g.get("division") or "fbs") != other]
    n_started = sum(1 for g in games if _started(g))
    games = [g for g in games if not _started(g)]
    if not games:
        return {"error_hint": "all_started", "n_started": n_started}
    try:
        idx = kalshi_cfb.index() or {}
    except Exception:
        idx = {}
    sel_map = {}
    for tok in (game_sel or ()):
        base, _, team = str(tok).partition(":")
        if base:
            sel_map[base] = team or True
    games_bundles = []
    for g in games:
        if abort_cb is not None and abort_cb():
            raise RuntimeError("superseded by a newer build")
        team_only = None
        if sel_map:
            v = sel_map.get(g.get("pair") or "", sel_map.get(g.get("suffix") or ""))
            if v is None:
                continue
            if v is not True:
                team_only = v
        cands = [dict(c) for c in g["cands"] if (types is None or c["type"] in types)]
        if not cands:
            continue
        price_cands(cands, g.get("suffix"), idx)
        if team_only:
            # One club picked: its own legs. ESPN abbr on the grid, Kalshi
            # code on the kref -- both are the team's, so accept either.
            want = {team_only, g["kx_home"] if team_only == g["home"] else
                    g["kx_away"] if team_only == g["away"] else team_only}
            cands = [c for c in cands if c.get("side_team") in want]
        if idx:
            cands = [c for c in cands if c.get("price_cents")]
        cands = [c for c in cands if floor <= c["marg"] <= ceil]
        if len(cands) > 40:
            cands.sort(key=lambda c: (
                c.get("price_cents") is None,
                -abs((c.get("marg") or 0) * 100 - (c.get("price_cents") or 50.0)),
                -(c.get("marg") or 0)))
            cands = cands[:40]
        if max_bet:
            cands = [c for c in cands
                     if combo_engine.stackable(c["marg"], c.get("price_cents"))]
        if not cands:
            continue
        depth = max(1, min(max_legs_per_game, max(n_legs, 3), max_total_legs))
        bundles = mlb_sim.game_bundles(cands, g["n"], max_legs=depth)
        if bundles:
            games_bundles.append((g["label"], bundles, g.get("suffix") or g.get("pair")))
    if not games_bundles:
        return None
    if len(games_bundles) < 2 and max_legs_per_game <= 1:
        return {"error_hint": "single_game_no_stack", "n_games_available": len(games_bundles)}
    _dp = combo_engine.dp_legs(
        n_legs, "off" if max_bet else legs_mode, max_total_legs,
        payout_mode="require" if max_bet else payout_mode)
    states = combo_engine.frontier(games_bundles, max_total_legs=_dp, net=True)
    if max_bet:
        targets = {}
        best, meta = combo_engine.max_bet(states, cap=cap_x)
    else:
        targets = {"legs_target": n_legs, "payout_target": target_payout,
                   "legs_mode": legs_mode, "payout_mode": payout_mode, "conn": conn}
        best, meta = combo_engine.choose(states, objective=objective, **targets)
    if not best:
        return None
    item = mlb_sim._mixed_item(best["sel"], games_bundles,
                               None if max_bet else
                               (target_payout if payout_mode != "off" else None))
    kick = {(g.get("suffix") or g.get("pair")): _iso_ts(g.get("date")) for g in games}
    for grp in item.get("groups") or []:
        for leg in grp.get("legs") or []:
            tk, close = kalshi_cfb.ticker_leg(idx, grp.get("suffix"), leg.get("kref"))
            leg["ticker"], leg["close_time"] = tk, close
            leg["start_ts"] = kick.get(grp.get("suffix"))
    for k, v in meta.items():
        if k != "objective" and v is not None:
            item[k] = v
    item["objective"] = "max_bet" if max_bet else objective
    item["legs_target"] = None if max_bet else (n_legs if legs_mode != "off" else None)
    if max_bet:
        item["payout_reached"] = meta.get("cap_reached")
        item["target_payout_x"] = None
    item["sport"] = "cfb"
    item["leg_floor_pct"] = round(floor * 100, 1)
    item["leg_cap_pct"] = round(ceil * 100, 1) if ceil < 1.0 else None
    item["excluded_started"] = n_started or None
    item["pricing_unavailable"] = not idx
    item["cost_x"] = round(best["cost"], 4)
    item["market_payout_x"] = round(best["payout"], 2) if best["payout"] else None
    item["ev_pct"] = round(best["ev"] * 100, 1) if best["ev"] is not None else None
    item["kelly_pct"] = round(combo_engine.kelly(best["prob"], best["cost"]) * 100, 2)
    item["priced_frac"] = round(best["priced_frac"], 2)
    item["priced_legs"] = best["priced"]
    if not max_bet:
        item["alternatives"] = combo_engine.compare(states, best, **targets)
    item["n_sims"] = _N
    # The real Kalshi payout off the legs' asks, fees in (kalshiPayout()).
    import kalshi
    payout, net, priced, total = 1.0, 1.0, 0, 0
    for grp in item.get("groups") or []:
        for leg in grp.get("legs") or []:
            total += 1
            c = leg.get("market_cents")
            if c and 0 < c < 100:
                leg["market_payout_x"] = round(100.0 / c, 2)
                payout *= 100.0 / c
                net *= 100.0 / min(99.9, c + kalshi.taker_fee_cents(c))
                priced += 1
            else:
                leg["market_payout_x"] = None
    item.update({"kalshi_payout_x": round(payout, 2) if priced else None,
                 "kalshi_payout_net_x": round(net, 2) if priced else None,
                 "kalshi_priced": priced, "kalshi_total_legs": total,
                 "kalshi_full": priced == total and priced > 0})
    return item
