"""Golf tournament simulator — strokes model + Monte Carlo of the remaining
rounds, the golf twin of the other sport engines.

  DATA   ESPN public JSON:
           * the live leaderboard (field, each player's score to par, the round
             in progress and holes played, the cut round) and
           * each player's SEASON SCORING AVERAGE (core API) — the skill signal
             (cupPoints/earnings come back empty, scoring average doesn't).
         Course par + yardage come with the leaderboard; the course city is
         geocoded (Open-Meteo) for ELEVATION and WEATHER (wind, temperature)
         via the shared weather module.
  MODEL  Each player's expected strokes-to-par per round = the field-relative
         edge from his scoring average, amplified on longer/harder courses
         (skill matters more), shifted by conditions (wind and altitude move
         scoring; wind and extreme heat widen the round-to-round variance).
  SIM    Play the remaining rounds many times (each round a normal draw around
         the player's mean), apply the 36-hole CUT from the simulated field, and
         tally win / top-5 / top-10 / top-20 / make-the-cut plus every head-to-
         head (P(A finishes ahead of B)) — which is exactly what Kalshi prices
         (KXPGAH2H matchups, KXPGAMAKECUT).

Everything degrades gracefully: no scoring average -> field average; no
geocode/weather -> calm baseline. Relative finish (what the markets price) is
driven by the skill spread and variance, so the model is meaningful even when a
condition feed is missing.
"""

import concurrent.futures as _cf
import math
import random
import time as _t

import clock
import racing

_SITE = "https://site.api.espn.com/apis/site/v2/sports/golf"
_CORE = "https://sports.core.api.espn.com/v2/sports/golf/leagues"
_TOUR_PATH = {"pga": "pga", "lpga": "lpga", "euro": "eur", "champ": "champions-tour"}
_TOUR_PAR = 71.0          # nominal par the tour scoring average is measured against
_ROUND_SD = 2.85          # a tour pro's round-to-round scoring standard deviation


def _season():
    return clock.today_et().year


# ---- Field + live scores + course -------------------------------------------
def leaderboard(tour="pga"):
    # The generic /golf/leaderboard returns the featured PGA event with full
    # detail (athlete ids, linescores, course); other tours use their scoreboard.
    path = _TOUR_PATH.get(tour, "pga")
    url = (f"{_SITE}/leaderboard" if tour == "pga"
           else f"{_SITE}/{path}/scoreboard")

    def build():
        try:
            return racing._get_json(url, timeout=25)
        except Exception:
            return None
    return racing._cached(("golf_lb", tour), 300, build)


def field(tour="pga"):
    """{event, course, tournament meta, players:[...]} from the live leaderboard.
    Each player: {id, name, to_par, round_now, thru, made_cut(None until known)}."""
    d = leaderboard(tour)
    ev = (d or {}).get("events") or []
    if not ev:
        return None
    e = ev[0]
    comp = (e.get("competitions") or [{}])[0]
    tinfo = e.get("tournament") or {}
    crs = (e.get("courses") or [{}])[0]
    status = comp.get("status") or {}
    round_now = int(status.get("period") or 1)
    state = ((status.get("type") or {}).get("state"))
    players = []
    for c in comp.get("competitors", []):
        ath = c.get("athlete") or {}
        if not ath.get("id"):
            continue
        stt = c.get("status") or {}
        stat = {s["name"]: s.get("value") for s in (c.get("statistics") or [])}
        to_par = stat.get("scoreToPar")
        if to_par is None:
            to_par = 0.0
        pos = (stt.get("position") or {}).get("displayName") or ""
        # ESPN marks a missed cut as position "CUT" / status type "cut".
        cut_state = (stt.get("type") or {}).get("name", "").lower()
        missed = "cut" in cut_state or pos.upper() in ("CUT", "MC", "WD", "DQ")
        players.append({
            "id": str(ath["id"]), "name": ath.get("displayName"),
            "to_par": float(to_par), "thru": int(stt.get("thru") or 0),
            "position": pos, "missed": missed})
    return {
        "event": e.get("name"), "state": state, "round_now": round_now,
        "n_rounds": int(tinfo.get("numberOfRounds") or 4),
        "cut_round": int(tinfo.get("cutRound") or 2),
        "major": bool(tinfo.get("major")),
        "course": {"name": crs.get("name"), "par": float(crs.get("shotsToPar") or 71),
                   "yards": int(crs.get("totalYards") or 7200),
                   "address": crs.get("address") or {}},
        "players": players}


# ---- Skill: season scoring average per athlete ------------------------------
def _scoring_avg(aid, season):
    try:
        s = racing._get_json(
            f"{_CORE}/pga/seasons/{season}/types/2/athletes/{aid}/statistics", timeout=15)
    except Exception:
        return None
    for cat in (s.get("splits", {}) or {}).get("categories", []):
        for st in cat.get("stats", []):
            if st.get("name") == "scoringAverage":
                try:
                    return float(st.get("value"))
                except (TypeError, ValueError):
                    return None
    return None


def skills(ids, season=None):
    """{athlete_id: scoring_average} for the field (parallel, cached 12h). Falls
    back to last season's average when the current one hasn't populated yet, so
    established players aren't treated as unknowns."""
    season = season or _season()
    ids = list(ids)

    def one(aid):
        return _scoring_avg(aid, season) or _scoring_avg(aid, season - 1)

    def build():
        out = {}
        with _cf.ThreadPoolExecutor(max_workers=12) as ex:
            for aid, sa in zip(ids, ex.map(one, ids)):
                if sa:
                    out[aid] = sa
        return out
    key = ("golf_skill", season, hash(tuple(sorted(ids))))
    return racing._cached(key, 12 * 3600, build) or {}


# ---- Conditions: geocode -> elevation + weather -----------------------------
_US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia"}


def _geocode(city, state, country="US"):
    if not city:
        return None, None
    want = _US_STATES.get((state or "").upper())      # 2-letter -> full state name
    try:
        q = f"{city}".replace(" ", "+")
        g = racing._get_json(
            f"https://geocoding-api.open-meteo.com/v1/search?name={q}&count=10&country={country}",
            timeout=12)
        results = g.get("results") or []
        if want:
            for r in results:
                if (r.get("admin1") or "") == want:
                    return r.get("latitude"), r.get("longitude")
        r = results[0] if results else {}
        return r.get("latitude"), r.get("longitude")
    except Exception:
        return None, None


def _elevation_ft(lat, lon):
    try:
        d = racing._get_json(
            f"https://api.open-meteo.com/v1/elevation?latitude={lat:.3f}&longitude={lon:.3f}",
            timeout=12)
        m = (d.get("elevation") or [0])[0]
        return round((m or 0) * 3.28084)
    except Exception:
        return 0


def conditions(course):
    """Elevation + weather at the course, folded into a scoring shift and a
    variance multiplier. Best-effort; calm baseline when a feed is missing."""
    addr = course.get("address") or {}

    def build():
        lat, lon = _geocode(addr.get("city"), addr.get("state"))
        wind = temp = None
        elev = 0
        if lat and lon:
            elev = _elevation_ft(lat, lon)
            try:
                import weather
                wx = weather.get_weather(lat, lon, _t.time() + 6 * 3600)  # ~next tee window
                if wx:
                    wind, temp = wx.get("wind_mph"), wx.get("temp_f")
            except Exception:
                pass
        wind = 8.0 if wind is None else float(wind)
        temp = 72.0 if temp is None else float(temp)
        # Scoring: wind and cold play the course harder; altitude (thin air, ball
        # carries) plays it a touch easier. Yardage over a 7,200 baseline adds a
        # little too. Shown for context; relative finish is variance-driven.
        yards = course.get("yards") or 7200
        score_adj = (max(0.0, wind - 8.0) * 0.055
                     - (elev / 1000.0) * 0.15
                     + max(0.0, (yards - 7200) / 200.0) * 0.05
                     - max(0.0, (temp - 78) * 0.01))
        # Variance: wind and extreme heat scatter the field; a long course lets
        # skill separate (favorites firmer). Modeled as a spread multiplier.
        var_mult = 1.0 + max(0.0, wind - 8.0) * 0.02 + max(0.0, (temp - 90) * 0.01)
        skill_scale = 1.0 + max(-0.15, min(0.35, (yards - 7200) / 7200.0 * 0.6))
        return {"lat": lat, "lon": lon, "elev_ft": elev, "wind_mph": round(wind),
                "temp_f": round(temp), "yards": yards,
                "score_adj": round(score_adj, 2), "var_mult": round(var_mult, 3),
                "skill_scale": round(skill_scale, 3)}
    key = ("golf_cond", (addr.get("city") or "") + (addr.get("state") or ""))
    return racing._cached(key, 3 * 3600, build) or {
        "score_adj": 0.0, "var_mult": 1.0, "skill_scale": 1.0, "wind_mph": 8, "temp_f": 72}


# ---- Monte Carlo of the remaining rounds ------------------------------------
def _units(round_now, thru, n_rounds, cut_round):
    """Remaining scoring as (round_index, weight) units — a partial current round
    plus the full rounds after it. weight is the fraction of a round left."""
    units = []
    if round_now <= n_rounds:
        cur_w = max(0.0, (18 - thru) / 18.0)
        if cur_w > 0:
            units.append((round_now, cur_w))
        for r in range(round_now + 1, n_rounds + 1):
            units.append((r, 1.0))
    return units


def simulate(tour="pga", n=3000, seed=None):
    fd = field(tour)
    if not fd or not fd["players"]:
        return None
    ps = [p for p in fd["players"]]
    ids = [p["id"] for p in ps]
    sa = skills(ids)
    cond = conditions(fd["course"])
    # Field-relative skill: better (lower) scoring average => negative to-par mean.
    vals = [sa[i] for i in ids if i in sa]
    field_mean = sum(vals) / len(vals) if vals else _TOUR_PAR
    scale = cond.get("skill_scale", 1.0)
    adj = cond.get("score_adj", 0.0)
    sd = _ROUND_SD * cond.get("var_mult", 1.0)

    # A player with no scoring average in either season is typically an amateur /
    # Monday qualifier / injury return — weaker than the field on average, so his
    # prior sits a little above the field mean rather than at it.
    _MISS_PEN = 1.1
    means = []
    for p in ps:
        s = sa.get(p["id"], field_mean + _MISS_PEN)
        dev = field_mean - s                     # >0 = better than the field
        means.append(-dev * scale + adj)         # expected strokes-to-par / round

    n_rounds, cut_round = fd["n_rounds"], fd["cut_round"]
    round_now = fd["round_now"]
    # Per-player remaining units (thru can differ across players mid-round).
    per_unit = [_units(round_now, p["thru"], n_rounds, cut_round) for p in ps]
    cut_ahead = any(ri <= cut_round for u in per_unit for ri, _w in u)

    rng = random.Random(seed)
    gauss = rng.gauss
    np_ = len(ps)
    # Aggregates.
    made = [0] * np_
    win = [0] * np_
    t5 = [0] * np_
    t10 = [0] * np_
    t20 = [0] * np_
    finals = [[0.0] * n for _ in range(np_)]     # for head-to-head
    BIG = 1e6

    for s in range(n):
        cut_tot = [0.0] * np_
        fin_tot = [0.0] * np_
        for i, p in enumerate(ps):
            base = p["to_par"]
            m, u = means[i], per_unit[i]
            through_cut = base
            total = base
            for ri, w in u:
                draw = gauss(m * w, sd * math.sqrt(w))
                total += draw
                if ri <= cut_round:
                    through_cut += draw
            cut_tot[i] = through_cut
            fin_tot[i] = total
            if p["missed"]:
                cut_tot[i] = fin_tot[i] = BIG      # already gone
        # Apply the 36-hole cut from the simulated field (top 65 & ties) when it
        # is still ahead; otherwise everyone still listed plays on.
        if cut_ahead:
            order = sorted(range(np_), key=lambda i: cut_tot[i])
            keep = min(np_, 65)
            cutline = cut_tot[order[keep - 1]] if keep <= np_ else BIG
            for i in range(np_):
                if cut_tot[i] <= cutline and not ps[i]["missed"]:
                    made[i] += 1
                else:
                    fin_tot[i] = BIG               # missed cut -> out of the money
        else:
            for i in range(np_):
                if not ps[i]["missed"]:
                    made[i] += 1
        for i in range(np_):
            finals[i][s] = fin_tot[i]
        order = sorted(range(np_), key=lambda i: fin_tot[i])
        win[order[0]] += 1
        for rank, i in enumerate(order):
            if fin_tot[i] >= BIG:
                break
            if rank < 5:
                t5[i] += 1
            if rank < 10:
                t10[i] += 1
            if rank < 20:
                t20[i] += 1

    out = []
    for i, p in enumerate(ps):
        out.append({
            "id": p["id"], "name": p["name"], "to_par": p["to_par"],
            "position": p["position"], "scoring_avg": round(sa.get(p["id"], 0) or 0, 2) or None,
            "win_pct": round(100 * win[i] / n, 2),
            "top5_pct": round(100 * t5[i] / n, 1),
            "top10_pct": round(100 * t10[i] / n, 1),
            "top20_pct": round(100 * t20[i] / n, 1),
            "make_cut_pct": round(100 * made[i] / n, 1)})
    out.sort(key=lambda r: -r["win_pct"])
    return {"event": fd["event"], "course": fd["course"]["name"], "state": fd["state"],
            "round_now": round_now, "n_rounds": n_rounds, "cut_round": cut_round,
            "cut_ahead": cut_ahead, "major": fd["major"],
            "conditions": cond, "n_sims": n, "players": out,
            "_finals": finals, "_ids": ids, "_names": [p["name"] for p in ps]}


# ---- Head-to-head from the simulated finals ---------------------------------
def _norm(name):
    return "".join(ch for ch in (name or "").lower() if ch.isalnum() or ch == " ").strip()


def _h2h(sim, i, j):
    """P(player index i finishes ahead of j) over the simulated finals."""
    a, b = sim["_finals"][i], sim["_finals"][j]
    n = len(a)
    ahead = sum(1 for s in range(n) if a[s] < b[s])
    tie = sum(1 for s in range(n) if a[s] == b[s])
    return (ahead + 0.5 * tie) / n if n else 0.5


# ---- Kalshi pricing ---------------------------------------------------------
def _kalshi_markets(series):
    import kalshi
    out, cursor = [], None
    for _ in range(6):
        url = f"{kalshi.BASE}/markets?series_ticker={series}&status=open&limit=200"
        if cursor:
            url += f"&cursor={cursor}"
        try:
            d = kalshi._get_json(url)
        except Exception:
            break
        out.extend(d.get("markets") or [])
        cursor = d.get("cursor")
        if not cursor:
            break
    return out


def _price_board(sim):
    """Attach live Kalshi cents + edges: make-the-cut per player, and head-to-head
    matchups when those markets are open."""
    import kalshi
    name_idx = {_norm(sim["_names"][i]): i for i in range(len(sim["_names"]))}
    by_name = {p["name"]: p for p in sim["players"]}

    # Make-the-cut: one market per player (yes = makes the cut).
    mc_rows = []
    for m in _kalshi_markets("KXPGAMAKECUT"):
        nm = (m.get("yes_sub_title") or "").strip()
        p = by_name.get(nm) or by_name.get(next((k for k in by_name if _norm(k) == _norm(nm)), ""))
        if not p:
            continue
        cents = kalshi._cents(m.get("yes_ask_dollars"))
        mc_rows.append({"player": p["name"], "model_pct": p["make_cut_pct"],
                        "cents": cents, "ticker": m.get("ticker"),
                        "edge": round(p["make_cut_pct"] - cents, 1) if cents is not None else None})
    mc_rows.sort(key=lambda r: -(r["edge"] if r["edge"] is not None else -99))

    # Head-to-head matchups: each market names two players ("A vs B" / "Will A
    # beat B?"); price the YES side with the sim's P(named player ahead).
    h2h_rows = []
    for m in _kalshi_markets("KXPGAH2H"):
        title = f"{m.get('title','')} {m.get('yes_sub_title','')}"
        hit = [nm for nm in name_idx if nm and nm in _norm(title)]
        if len(hit) < 2:
            continue
        hit.sort(key=lambda nm: _norm(title).index(nm))
        ai, bi = name_idx[hit[0]], name_idx[hit[1]]
        p = _h2h(sim, ai, bi) * 100.0
        cents = kalshi._cents(m.get("yes_ask_dollars"))
        h2h_rows.append({"a": sim["_names"][ai], "b": sim["_names"][bi],
                         "model_pct": round(p, 1), "cents": cents,
                         "ticker": m.get("ticker"),
                         "edge": round(p - cents, 1) if cents is not None else None})
    h2h_rows.sort(key=lambda r: -(r["edge"] if r["edge"] is not None else -99))
    return {"make_cut": mc_rows, "h2h": h2h_rows}


# ---- Non-blocking board (cached; built in the background) --------------------

_cache = {}
_inflight = set()


def board(tour="pga"):
    import boardshare
    return boardshare.nonblocking(f"golf_{tour}", 900, _cache,
                                  ("golf_board", tour),
                                  lambda: _build_board(tour),
                                  "GOLF-board-build")


def _build_board(tour="pga", n=3000):
    sim = simulate(tour, n=n)
    if not sim:
        return None
    try:
        priced = _price_board(sim)
    except Exception:
        priced = {"make_cut": [], "h2h": []}
    # Log make-cut model probs for the calibrator (dedups by ticker).
    try:
        import predlog
        rows = [(r["ticker"], r["model_pct"] / 100.0, None)
                for r in priced["make_cut"] if r.get("ticker")]
        if rows:
            predlog.init_db()
            predlog.log_many("golf", rows)
    except Exception:
        pass
    players = [{k: p[k] for k in ("name", "to_par", "position", "scoring_avg",
                                  "win_pct", "top5_pct", "top10_pct", "top20_pct",
                                  "make_cut_pct")} for p in sim["players"]]
    return {"event": sim["event"], "course": sim["course"], "state": sim["state"],
            "round_now": sim["round_now"], "n_rounds": sim["n_rounds"],
            "cut_round": sim["cut_round"], "cut_ahead": sim["cut_ahead"],
            "major": sim["major"], "conditions": sim["conditions"],
            "n_sims": sim["n_sims"], "players": players,
            "make_cut": priced["make_cut"], "h2h": priced["h2h"],
            "note": "Strokes model: each player's season scoring average sets his "
                    "per-round mean, amplified by course length and shifted by wind, "
                    "temperature and altitude; the remaining rounds are simulated "
                    "with the 36-hole cut to win / top-N / make-cut and every "
                    "head-to-head, priced against Kalshi."}
