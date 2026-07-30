"""NHL: the full baseball-style stack for hockey.

Same architecture as basket.py, with hockey mechanics:

  DATA      ESPN — teams, standings (goals for/against per game), per-player
            season averages (goals/assists/points per game).
  MODEL     Goals matchup: each side's expected goals = its scoring rate vs the
            opponent's goals-allowed around the league mean, with home ice.
  SIM       Shot-event Monte Carlo: three periods of shot events (goals ride a
            per-shot conversion tuned to the matchup), a one-goal game's
            empty-net dynamics, 3-on-3 OT sudden death and the shootout.
            Team goals are dealt back to skaters by scoring share, so player
            goal/assist/point props correlate with the team result.
  MARKETS   Kalshi KXNHLGAME / KXNHLSPREAD / KXNHLTOTAL — offseason now, so the
            index auto-lights-up the moment the season's series list (same
            suffix + city matching as basketball).
  COMBOS    Bitmask legs feed the shared MLB parlay machinery for same-game
            parlays.
  RECORD    Priced moneylines log their RAW model prob to predlog; the site
            calibrator carries an 'nhl' temperature.

quick_game() is the deep-season resolver: ONE fast game returning goals + the
overtime flag, so the nightly 4000-season Monte Carlo can award real NHL
standings points (2 win / 1 OT loss).
"""
import math
import random
import re
import threading
import time

import clock
import racing

_SITE = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl"
_BYATH = ("https://site.web.api.espn.com/apis/common/v3/sports/hockey/nhl/"
          "statistics/byathlete?region=us&lang=en&contentorigin=espn"
          "&limit=50&sort=offensive.points:desc&page={page}")

_LG_GOALS = 3.05                 # league goals/team/game fallback
_HCA_G = 1.045                   # home-ice: ~±4.5% on scoring (~0.27 goals)
_SHOTS = 30.0                    # shot events per side per game
_REGRESS_G = 10.0
_OT_SHARE = 0.65                 # share of ties decided in OT (rest: shootout)


# ---- Teams / ratings --------------------------------------------------------
def teams():
    def build():
        try:
            d = racing._get_json(f"{_SITE}/teams", timeout=15)
            ts = d["sports"][0]["leagues"][0]["teams"]
        except Exception:
            return None
        out = {}
        for t in ts:
            t = t["team"]
            out[t["abbreviation"]] = {
                "id": t["id"], "abbr": t["abbreviation"],
                "name": t.get("displayName"), "location": (t.get("location") or "").lower(),
                "nick": (t.get("name") or "").lower()}
        return out or None
    return racing._cached(("nhl_teams",), 24 * 3600, build) or {}


def ratings():
    """{abbr: {w, l, g, gf, ga, lg_g}} — goals for/against per game from the
    standings, regressed early. Cached 3h."""
    def build():
        try:
            d = racing._get_json(
                "https://site.api.espn.com/apis/v2/sports/hockey/nhl/standings",
                timeout=15)
        except Exception:
            return None
        tmap = teams()
        by_id = {v["id"]: v for v in tmap.values()}
        rows = {}
        for grp in d.get("children", []):
            for e in grp.get("standings", {}).get("entries", []):
                st = {s["name"]: s.get("value") for s in e.get("stats", [])}
                tid = e["team"]["id"]
                ab = (by_id.get(tid) or {}).get("abbr") or e["team"].get("abbreviation")
                w, l = int(st.get("wins") or 0), int(st.get("losses") or 0)
                g = int(st.get("gamesPlayed") or (w + l) or 1)
                gf = st.get("pointsFor") or 0            # ESPN generic name = goals
                ga = st.get("pointsAgainst") or 0
                if not gf or not g:
                    continue
                rows[ab] = {"abbr": ab, "id": tid, "w": w, "l": l, "g": g,
                            "gf": gf / g, "ga": ga / g}
        if not rows:
            return None
        lg_g = sum(r["gf"] for r in rows.values()) / len(rows)
        for r in rows.values():
            wgt = r["g"] / (r["g"] + _REGRESS_G)
            r["gf"] = wgt * r["gf"] + (1 - wgt) * lg_g
            r["ga"] = wgt * r["ga"] + (1 - wgt) * lg_g
            r["lg_g"] = lg_g
        return rows
    return racing._cached(("nhl_ratings",), 3 * 3600, build) or {}


# ---- Players ----------------------------------------------------------------
def players():
    """{abbr: [top skaters]} with {name, gpg, apg, ptspg, gp}. Cached 6h."""
    def build():
        out = {}
        for page in range(1, 16):
            try:
                d = racing._get_json(_BYATH.format(page=page), timeout=20)
            except Exception:
                break
            aths = d.get("athletes") or []
            if not aths:
                break
            names = {c.get("name"): c.get("names") or []
                     for c in (d.get("categories") or [])}
            for a in aths:
                ath = a.get("athlete") or {}
                ab = (ath.get("teamShortName")
                      or (ath.get("team") or {}).get("abbreviation"))
                if not ab:
                    continue
                row = {"name": ath.get("displayName"), "gpg": 0.0, "apg": 0.0,
                       "ptspg": 0.0, "gp": 0}
                raw = {"goals": 0.0, "assists": 0.0, "points": 0.0}
                for c in a.get("categories") or []:
                    m = {n: v for n, v in zip(names.get(c.get("name")) or [],
                                              c.get("totals") or [])}

                    def f(key):
                        try:
                            return float(m.get(key))
                        except (TypeError, ValueError):
                            return None
                    if c.get("name") == "offensive":
                        raw["goals"] = f("goals") or 0.0
                        raw["assists"] = f("assists") or 0.0
                        raw["points"] = f("points") or 0.0
                    elif c.get("name") == "general":
                        row["gp"] = int(f("games") or 0)
                gp = row["gp"]
                if gp and raw["points"] > 0:
                    row["gpg"] = round(raw["goals"] / gp, 3)
                    row["apg"] = round(raw["assists"] / gp, 3)
                    row["ptspg"] = round(raw["points"] / gp, 3)
                    out.setdefault(ab, []).append(row)
            if len(aths) < 50:
                break
        for ab in out:
            out[ab].sort(key=lambda p: -p["ptspg"])
            out[ab] = out[ab][:9]
        return out or None
    return racing._cached(("nhl_players",), 6 * 3600, build) or {}


# ---- Schedule ---------------------------------------------------------------
def schedule(date=None):
    date = date or clock.today_et().isoformat()
    ymd = date.replace("-", "")

    def build():
        try:
            d = racing._get_json(f"{_SITE}/scoreboard?dates={ymd}", timeout=15)
        except Exception:
            return None
        out = []
        for e in d.get("events", []):
            comp = (e.get("competitions") or [{}])[0]
            cs = comp.get("competitors", [])
            home = next((c for c in cs if c.get("homeAway") == "home"), None)
            away = next((c for c in cs if c.get("homeAway") == "away"), None)
            if not home or not away:
                continue
            st = (comp.get("status", {}) or {}).get("type", {})
            out.append({
                "home": home["team"]["abbreviation"], "away": away["team"]["abbreviation"],
                "home_name": home["team"].get("shortDisplayName"),
                "away_name": away["team"].get("shortDisplayName"),
                "date": e.get("date"), "state": st.get("state"),
                "detail": st.get("shortDetail"),
                "home_score": home.get("score"), "away_score": away.get("score")})
        return out
    return racing._cached(("nhl_sched", ymd), 300, build) or []


# ---- Kalshi (auto-lights-up when the season's series list) -------------------
_SPREAD_RE = re.compile(r"^(.*?)\s+wins by over", re.I)


def _kalshi_index():
    import kalshi

    def fetch(series):
        out, cursor = [], None
        for _ in range(4):
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

    def build():
        idx = {}

        def ev(m):
            suf = (m.get("event_ticker") or "").split("-", 1)[-1]
            return idx.setdefault(suf, {"ml": {}, "spreads": [], "totals": []})

        import kalshi as _k
        for m in fetch("KXNHLGAME"):
            city = (m.get("yes_sub_title") or "").strip().lower()
            if city:
                ev(m)["ml"][city] = {"cents": _k._cents(m.get("yes_ask_dollars")),
                                     "ticker": m.get("ticker"),
                                     "close": _k._parse_time(m.get("close_time"))}
        for m in fetch("KXNHLSPREAD"):
            t = _SPREAD_RE.match(m.get("yes_sub_title") or m.get("title") or "")
            line = m.get("floor_strike")
            if t and line is not None:
                ev(m)["spreads"].append({"city": t.group(1).strip().lower(),
                                         "line": float(line),
                                         "cents": _k._cents(m.get("yes_ask_dollars"))})
        for m in fetch("KXNHLTOTAL"):
            line = m.get("floor_strike")
            if line is not None:
                ev(m)["totals"].append({"line": float(line),
                                        "cents": _k._cents(m.get("yes_ask_dollars"))})
        return idx
    return racing._cached(("nhl_kalshi",), 120, build) or {}


def _city_match(city, meta):
    return bool(city) and (city.startswith(meta["location"])
                           or meta["location"].startswith(city)
                           or (meta["nick"] and meta["nick"] in city))


def game_markets(home_ab, away_ab):
    tmap = teams()
    hm, am = tmap.get(home_ab), tmap.get(away_ab)
    if not hm or not am:
        return None
    for suf, e in _kalshi_index().items():
        cities = list(e["ml"].keys())
        if len(cities) < 2:
            continue
        h = next((c for c in cities if _city_match(c, hm)), None)
        a = next((c for c in cities if _city_match(c, am)), None)
        if not h or not a:
            continue
        out = {"home_ml": e["ml"][h], "away_ml": e["ml"][a],
               "spreads": [], "totals": sorted(e["totals"], key=lambda t: t["line"])}
        for s in e["spreads"]:
            side = ("home" if _city_match(s["city"], hm)
                    else "away" if _city_match(s["city"], am) else None)
            if side:
                out["spreads"].append(dict(s, side=side))
        return out
    return None


# ---- Shot-event simulator ---------------------------------------------------
def _period_goals(exp_g, rng):
    """Goals in one period: shot events with a per-shot conversion."""
    shots = max(1, int(round(rng.gauss(_SHOTS / 3.0, 2.2))))
    p = min(0.55, (exp_g / 3.0) / (_SHOTS / 3.0))
    g = 0
    for _ in range(shots):
        if rng.random() < p:
            g += 1
    return g


def _regulation(exp_h, exp_a, rng):
    h = a = 0
    for _ in range(3):
        h += _period_goals(exp_h, rng)
        a += _period_goals(exp_a, rng)
    # Empty-net dynamics: a one-goal lead late often becomes two (ENG) and
    # occasionally evaporates (goalie pulled, equalizer).
    if abs(h - a) == 1:
        r = rng.random()
        if r < 0.28:                             # empty-netter seals it
            if h > a:
                h += 1
            else:
                a += 1
        elif r < 0.40:                           # 6-on-5 equalizer forces OT
            if h > a:
                a += 1
            else:
                h += 1
    return h, a


def quick_game(exp_h, exp_a, rng):
    """(home_goals, away_goals, went_ot) — the deep-season resolver. went_ot
    lets the season sim award real NHL points (2 win / 1 OT-SO loss)."""
    h, a = _regulation(exp_h, exp_a, rng)
    if h != a:
        return h, a, False
    p_h = exp_h / (exp_h + exp_a)
    if rng.random() < _OT_SHARE:                 # 3-on-3 OT sudden death
        if rng.random() < p_h:
            return h + 1, a, True
        return h, a + 1, True
    if rng.random() < 0.5 + (p_h - 0.5) * 0.5:   # shootout: skill matters less
        return h + 1, a, True
    return h, a + 1, True


def _shares(plist):
    tot = sum(p["ptspg"] for p in plist) or 1.0
    return [(p, p["gpg"], p["apg"], p["ptspg"] / tot) for p in plist]


def simulate_game(rh, ra, ph_list, pa_list, n=3000, seed=None):
    """Full-detail correlated MC of one game (ladders, props, SGP legs)."""
    rng = random.Random(seed if seed is not None
                        else hash((rh["abbr"], ra["abbr"])) & 0xFFFFFFFF)
    lg = rh.get("lg_g") or _LG_GOALS
    exp_h = rh["gf"] * (ra["ga"] / lg) * _HCA_G
    exp_a = ra["gf"] * (rh["ga"] / lg) / _HCA_G

    sh_h, sh_a = _shares(ph_list), _shares(pa_list)
    n_ply = len(sh_h) + len(sh_a)
    margins, totals = [], []
    hw = 0
    goal_rows = [[0] * n for _ in range(n_ply)]
    pts_rows = [[0.0] * n for _ in range(n_ply)]

    for s in range(n):
        h, a, went_ot = quick_game(exp_h, exp_a, rng)
        if h > a:
            hw += 1
        margins.append(h - a)
        totals.append(h + a)
        off = 0
        for team_goals, shares, exp in ((h, sh_h, exp_h), (a, sh_a, exp_a)):
            perf = team_goals / max(0.5, exp)
            gsum = sum(g for _, g, _, _ in shares) or 1.0
            # deal each goal to a scorer by goal-rate share
            for _ in range(team_goals):
                r = rng.random() * gsum
                acc = 0.0
                for i, (_, g, _, _) in enumerate(shares):
                    acc += g
                    if r < acc:
                        goal_rows[off + i][s] += 1
                        break
            for i, (p, g, a_, _) in enumerate(shares):
                pts_rows[off + i][s] = (goal_rows[off + i][s]
                                        + a_ * perf * max(0.05, rng.gauss(1.0, 0.4)))
            off += len(shares)

    p_home = hw / n
    all_ps = [p for p, _, _, _ in sh_h] + [p for p, _, _, _ in sh_a]
    team_of = [rh["abbr"]] * len(sh_h) + [ra["abbr"]] * len(sh_a)
    mean_total = sum(totals) / n

    total_ladder = []
    for line in [round(mean_total) + d + 0.5 for d in (-2, -1, 0, 1, 2)]:
        over = sum(1 for t in totals if t > line) / n
        if 0.05 <= over <= 0.95:
            total_ladder.append({"line": line, "over_pct": round(over * 100, 1)})
    spread_ladder = {"home": {}, "away": {}}
    for m in (1.5, 2.5):
        spread_ladder["home"][str(m)] = round(100 * sum(1 for x in margins if x > m) / n, 1)
        spread_ladder["away"][str(m)] = round(100 * sum(1 for x in margins if x < -m) / n, 1)

    players_out, props, cands = [], [], []

    def add_cand(typ, label, pred, group, kref=None, avg=None, unit=None):
        mask = 0
        for i in range(n):
            if pred(i):
                mask |= (1 << i)
        marg = bin(mask).count("1") / n
        if 0.05 <= marg <= 0.96:
            cands.append({"type": typ, "label": label, "mask": mask, "marg": marg,
                          "group": group, "model_pct": None, "kref": kref,
                          "sim_avg": avg, "avg_unit": unit})

    hn, an = rh.get("name", rh["abbr"]), ra.get("name", ra["abbr"])
    add_cand("ML", f"{hn} to win", lambda i: margins[i] > 0, "ML",
             kref={"t": "ml", "team": rh["abbr"]},
             avg=round(sum(margins) / n, 2), unit="goal margin")
    add_cand("ML", f"{an} to win", lambda i: margins[i] < 0, "ML",
             kref={"t": "ml", "team": ra["abbr"]},
             avg=round(-sum(margins) / n, 2), unit="goal margin")
    for m in (1.5, 2.5):
        add_cand("Spread", f"{rh['abbr']} wins by {m}+",
                 lambda i, m=m: margins[i] > m, "Spread")
        add_cand("Spread", f"{ra['abbr']} wins by {m}+",
                 lambda i, m=m: margins[i] < -m, "Spread")
    for d in (-1, 0, 1):
        line = round(mean_total) + d + 0.5
        add_cand("Total", f"Over {line}", lambda i, L=line: totals[i] > L, "Total",
                 avg=round(mean_total, 2), unit="goals")
        add_cand("Total", f"Under {line}", lambda i, L=line: totals[i] < L, "Total",
                 avg=round(mean_total, 2), unit="goals")

    for i, p in enumerate(all_ps):
        garr, parr = goal_rows[i], pts_rows[i]
        g_mean = sum(garr) / n
        p_mean = sum(parr) / n
        row = {"name": p["name"], "team": team_of[i], "goals": round(g_mean, 2),
               "pts": round(p_mean, 2)}
        players_out.append(row)
        p_goal = sum(1 for x in garr if x >= 1) / n
        if p_goal >= 0.15:
            props.append({"player": p["name"], "team": team_of[i],
                          "stat": "anytime goal", "line": 0.5,
                          "over_pct": round(p_goal * 100, 1)})
            add_cand("Goal", f"{p['name']} scores", lambda i2, A=garr: A[i2] >= 1,
                     f"{p['name']}:g", avg=round(g_mean, 2), unit="goals")
        p_pt = sum(1 for x in parr if x >= 1) / n
        if 0.25 <= p_pt <= 0.95:
            props.append({"player": p["name"], "team": team_of[i],
                          "stat": "1+ point", "line": 0.5,
                          "over_pct": round(p_pt * 100, 1)})
            add_cand("Point", f"{p['name']} 1+ point", lambda i2, A=parr: A[i2] >= 1,
                     f"{p['name']}:p", avg=round(p_mean, 2), unit="points")

    return {"home": rh["abbr"], "away": ra["abbr"],
            "p_home": round(p_home, 4), "p_away": round(1 - p_home, 4),
            "exp_home": round(sum(totals[i] + margins[i] for i in range(n)) / (2 * n), 2),
            "exp_away": round(sum(totals[i] - margins[i] for i in range(n)) / (2 * n), 2),
            "exp_total": round(mean_total, 2),
            "mean_margin": round(sum(margins) / n, 2),
            "total_ladder": total_ladder, "spread_ladder": spread_ladder,
            "players": sorted(players_out, key=lambda r: -r["pts"])[:12],
            "props": sorted(props, key=lambda p: -p["over_pct"])[:14],
            "n_sims": n, "_margins": margins, "_totals": totals, "_cands": cands}


def same_game_parlay(sim, n_legs=3, target=0.45):
    import mlb_sim
    return mlb_sim.best_same_game(sim["_cands"], sim["n_sims"], n_legs, target,
                                  0, 5)


# ---- The daily board --------------------------------------------------------
_cache = {}
_inflight = set()


def board(date=None):
    # Out of season: no games to price, so do not fetch anything. See
    # pro_sim.SEASON_WINDOW.
    try:
        import pro_sim
        if not pro_sim.in_season("nhl"):
            return {"date": date or clock.today_et().isoformat(), "games": [],
                    "off_season": True, "league": "nhl"}
    except Exception:
        pass
    date = date or clock.today_et().isoformat()
    key = ("nhl_board", date)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    if key not in _inflight:
        _inflight.add(key)

        def _bg():
            try:
                val = _build_board(date)
                if val is not None:
                    _cache[key] = (time.time(), val)
            finally:
                _inflight.discard(key)
        threading.Thread(target=_bg, daemon=True).start()
    return hit[1] if hit else None


def _build_board(date, n=3000):
    sched = schedule(date)
    if not sched:
        return {"date": date, "games": [], "n_games": 0, "league": "nhl",
                "note": "No NHL games scheduled for this date."}
    rt = ratings()
    pl = players()
    if not rt:
        return None
    try:
        import calibrate
        cal = lambda p: max(0.03, min(0.97, calibrate.apply("nhl", p)))
    except Exception:
        cal = lambda p: p

    games, log_rows = [], []
    for g in sched:
        rh, ra = rt.get(g["home"]), rt.get(g["away"])
        if not rh or not ra:
            continue
        rh = dict(rh, name=g.get("home_name") or g["home"])
        ra = dict(ra, name=g.get("away_name") or g["away"])
        sim = simulate_game(rh, ra, pl.get(g["home"]) or [], pl.get(g["away"]) or [], n=n)
        raw_ph = sim["p_home"]
        ph = cal(raw_ph)
        row = {k: sim[k] for k in ("home", "away", "exp_home", "exp_away",
                                   "exp_total", "mean_margin", "total_ladder",
                                   "spread_ladder", "players", "props", "n_sims")}
        row.update({"home_name": g.get("home_name"), "away_name": g.get("away_name"),
                    "date": g.get("date"), "state": g.get("state"),
                    "detail": g.get("detail"),
                    "home_score": g.get("home_score"), "away_score": g.get("away_score"),
                    "p_home": round(ph, 4), "p_away": round(1 - ph, 4),
                    "p_home_raw": raw_ph,
                    "home_rec": f"{rh['w']}-{rh['l']}", "away_rec": f"{ra['w']}-{ra['l']}"})
        mk = None
        try:
            mk = game_markets(g["home"], g["away"])
        except Exception:
            mk = None
        if mk:
            kx = {"home_cents": (mk["home_ml"] or {}).get("cents"),
                  "away_cents": (mk["away_ml"] or {}).get("cents")}
            row["kalshi"] = kx
            if kx["home_cents"] is not None:
                row["edge_home"] = round(ph * 100 - kx["home_cents"], 1)
            if kx["away_cents"] is not None:
                row["edge_away"] = round((1 - ph) * 100 - kx["away_cents"], 1)
            sp_edges, margins, totals = [], sim["_margins"], sim["_totals"]
            nn = sim["n_sims"]
            for s in mk["spreads"]:
                if s.get("cents") is None:
                    continue
                if s["side"] == "home":
                    p = sum(1 for x in margins if x > s["line"]) / nn
                else:
                    p = sum(1 for x in margins if x < -s["line"]) / nn
                sp_edges.append({"side": s["side"], "line": s["line"],
                                 "team": row["home" if s["side"] == "home" else "away"],
                                 "cents": s["cents"], "model_pct": round(p * 100, 1),
                                 "edge": round(p * 100 - s["cents"], 1)})
            tot_edges = []
            for t in mk["totals"]:
                if t.get("cents") is None:
                    continue
                p = sum(1 for x in totals if x > t["line"]) / nn
                tot_edges.append({"line": t["line"], "cents": t["cents"],
                                  "model_pct": round(p * 100, 1),
                                  "edge": round(p * 100 - t["cents"], 1)})
            sp_edges.sort(key=lambda r: -abs(r["edge"]))
            tot_edges.sort(key=lambda r: -abs(r["edge"]))
            row["spread_edges"] = sp_edges[:4]
            row["total_edges"] = tot_edges[:4]
            for side, ml, p in (("home", mk["home_ml"], raw_ph),
                                ("away", mk["away_ml"], 1 - raw_ph)):
                if ml and ml.get("ticker"):
                    log_rows.append((ml["ticker"], p, ml.get("close")))

        pick_home = ph >= 0.5
        row["pick"] = {"team": row["home" if pick_home else "away"],
                       "name": (g.get("home_name") if pick_home else g.get("away_name")),
                       "pct": round((ph if pick_home else 1 - ph) * 100, 1)}
        try:
            row["sgp"] = same_game_parlay(sim)
        except Exception:
            row["sgp"] = None
        games.append(row)

    if log_rows:
        try:
            import predlog
            predlog.init_db()
            predlog.log_many("nhl", log_rows)
        except Exception:
            pass
    games.sort(key=lambda x: x["date"] or "")
    return {"date": date, "n_games": len(games), "games": games, "n_sims": n,
            "league": "nhl",
            "note": "Shot-event Monte Carlo: expected goals vs the opponent's "
                    "defense with home ice, empty-net endgames, 3-on-3 OT and "
                    "the shootout. Skater goal/point props are dealt from the "
                    "simulated team goals; spread/total edges price at Kalshi's "
                    "exact lines once the season's markets list."}
