"""Basketball core: the full baseball-style stack, league-parameterized.

One possession engine serves the NBA (it lights up in October; its Kalshi
game/spread/total series auto-price the moment they list). Everything hangs off a
league config: ESPN endpoints, Kalshi series names, pace/efficiency norms,
home-court edge and season labeling.

  DATA      ESPN — teams, standings (points for/against), per-team statistics
            (possessions -> pace + offensive/defensive efficiency), and one
            paged byathlete call for every player's PPG/RPG/APG.
  MODEL     Efficiency matchup: points-per-possession vs the opponent's defense
            around the league mean, at the game's blended pace, with home court;
            early-season teams regress toward league average by games played.
  SIM       Possession-level Monte Carlo: 0/1/2/3-point possessions tuned to the
            matchup efficiency; garbage time slows and damps a decided 4th
            quarter; ties go to overtime. Team points are dealt back to players
            by scoring share, so player props correlate with the team result.
  MARKETS   Kalshi game/spread/total series matched by event suffix + city
            names, priced at the EXACT listed lines for real edges.
  COMBOS    Bitmask legs feed the shared MLB parlay machinery for correlated
            same-game parlays.
  RECORD    Priced moneylines log their RAW model prob to predlog; the site
            calibrator fits a per-league temperature on the graded history.
"""
import math
import predlog
import random
import re
import threading
import time

import clock
import racing                       # shared cached-JSON getter

LEAGUES = {
    "nba":  {"path": "nba", "label": "🏀 NBA", "hca_eff": 1.010,
             "lg_ppp": 1.145, "lg_pace": 99.0, "regress_g": 12.0,
             "kx_ml": "KXNBAGAME", "kx_spread": "KXNBASPREAD",
             "kx_total": "KXNBATOTAL", "cal": "nba"},
}


def _site(lg):
    return f"https://site.api.espn.com/apis/site/v2/sports/basketball/{LEAGUES[lg]['path']}"


def _core(lg):
    return f"http://sports.core.api.espn.com/v2/sports/basketball/leagues/{LEAGUES[lg]['path']}"


def _byath(lg, page):
    p = LEAGUES[lg]["path"]
    return (f"https://site.web.api.espn.com/apis/common/v3/sports/basketball/{p}/"
            "statistics/byathlete?region=us&lang=en&contentorigin=espn"
            f"&limit=50&sort=offensive.avgPoints:desc&page={page}")


def _season(lg):
    t = clock.today_et()
    if lg == "nba":                     # NBA seasons are labeled by their END year
        return t.year + 1 if t.month >= 10 else t.year
    return t.year


# ---- Teams ------------------------------------------------------------------
def teams(lg):
    """{abbr: {id, abbr, name, location, nick}} — cached 24h."""
    def build():
        try:
            d = racing._get_json(f"{_site(lg)}/teams", timeout=15)
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
    return racing._cached(("bk_teams", lg), 24 * 3600, build) or {}


# ---- Ratings: efficiency + pace ---------------------------------------------
def _team_stats(lg, season, tid):
    def build():
        try:
            d = racing._get_json(
                f"{_core(lg)}/seasons/{season}/types/2/teams/{tid}/statistics?lang=en",
                timeout=15)
        except Exception:
            return None
        cats = {c["name"]: {s["name"]: s.get("value") for s in c.get("stats", [])}
                for c in (d.get("splits", {}) or {}).get("categories", [])}
        off, gen = cats.get("offensive", {}), cats.get("general", {})
        return {"fga": off.get("fieldGoalsAttempted"), "fta": off.get("freeThrowsAttempted"),
                "orb": gen.get("offensiveRebounds") or off.get("offensiveRebounds"),
                "to": off.get("turnovers") or gen.get("turnovers"),
                "g": off.get("gamesPlayed") or gen.get("gamesPlayed")}
    return racing._cached(("bk_tstat", lg, season, tid), 6 * 3600, build)


def ratings(lg):
    """{abbr: {w, l, g, pf, pa, pace, off_ppp, def_ppp, lg_ppp, lg_pace}} —
    standings + possession math, regressed to league average early. Cached 3h."""
    cfg = LEAGUES[lg]

    def build():
        try:
            d = racing._get_json(
                f"https://site.api.espn.com/apis/v2/sports/basketball/{cfg['path']}/standings",
                timeout=15)
        except Exception:
            return None
        tmap = teams(lg)
        by_id = {v["id"]: v for v in tmap.values()}
        rows = {}
        for grp in d.get("children", []):
            for e in grp.get("standings", {}).get("entries", []):
                st = {s["name"]: s.get("value") for s in e.get("stats", [])}
                tid = e["team"]["id"]
                ab = (by_id.get(tid) or {}).get("abbr") or e["team"].get("abbreviation")
                w, l = int(st.get("wins") or 0), int(st.get("losses") or 0)
                g = max(1, w + l)
                rows[ab] = {"abbr": ab, "id": tid, "w": w, "l": l, "g": g,
                            "pf": float(st.get("avgPointsFor") or 0.0),
                            "pa": float(st.get("avgPointsAgainst") or 0.0)}
        rows = {ab: r for ab, r in rows.items() if r["pf"] > 0}
        if not rows:
            return None
        season = _season(lg)
        for ab, r in rows.items():
            ts = _team_stats(lg, season, r["id"]) or {}
            g = ts.get("g") or r["g"]
            try:
                poss = (ts["fga"] - (ts["orb"] or 0) + (ts["to"] or 0)
                        + 0.44 * (ts["fta"] or 0)) / max(1, g)
            except (KeyError, TypeError):
                poss = cfg["lg_pace"]
            r["pace"] = max(cfg["lg_pace"] - 12, min(cfg["lg_pace"] + 12,
                                                     poss or cfg["lg_pace"]))
        lg_pf = sum(r["pf"] for r in rows.values()) / len(rows)
        lg_pace = sum(r["pace"] for r in rows.values()) / len(rows)
        lg_ppp = lg_pf / lg_pace if lg_pace else cfg["lg_ppp"]
        for r in rows.values():
            w = r["g"] / (r["g"] + cfg["regress_g"])     # early-season regression
            off = (r["pf"] / r["pace"]) if r["pace"] else lg_ppp
            dfn = (r["pa"] / r["pace"]) if r["pace"] else lg_ppp
            r["off_ppp"] = w * off + (1 - w) * lg_ppp
            r["def_ppp"] = w * dfn + (1 - w) * lg_ppp
            r["lg_ppp"] = lg_ppp
            r["lg_pace"] = lg_pace
        return rows
    return racing._cached(("bk_ratings", lg), 3 * 3600, build) or {}


# ---- Players: league-wide per-game averages ---------------------------------
def players(lg):
    """{abbr: [players sorted by ppg]} with {name, ppg, rpg, apg, gp}. Paged
    byathlete call for the whole league. Cached 6h."""
    pages = 14                               # NBA rosters need every page

    def build():
        out = {}
        for page in range(1, pages):
            try:
                d = racing._get_json(_byath(lg, page), timeout=20)
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
                row = {"name": ath.get("displayName"), "ppg": 0.0, "rpg": 0.0,
                       "apg": 0.0, "gp": 0}
                for c in a.get("categories") or []:
                    vals = c.get("totals") or []
                    nms = names.get(c.get("name")) or []
                    m = {n: v for n, v in zip(nms, vals)}

                    def f(key):
                        try:
                            return float(m.get(key))
                        except (TypeError, ValueError):
                            return None
                    if c.get("name") == "offensive":
                        row["ppg"] = f("avgPoints") or row["ppg"]
                        row["apg"] = f("avgAssists") or row["apg"]
                    elif c.get("name") == "general":
                        row["rpg"] = f("avgRebounds") or row["rpg"]
                        row["gp"] = int(f("gamesPlayed") or 0)
                    elif c.get("name") == "defensive":
                        row["rpg"] = row["rpg"] or f("avgRebounds") or 0.0
                if row["ppg"] > 0:
                    out.setdefault(ab, []).append(row)
            if len(aths) < 50:
                break
        for ab in out:
            out[ab].sort(key=lambda p: -p["ppg"])
            out[ab] = out[ab][:9]
        return out or None
    return racing._cached(("bk_players", lg), 6 * 3600, build) or {}


# ---- Schedule ---------------------------------------------------------------
def schedule(lg, date=None):
    """[{home, away, ids, names, date, state, scores}] for a date. Cached 5m."""
    date = date or clock.today_et().isoformat()
    ymd = date.replace("-", "")

    def build():
        try:
            d = racing._get_json(f"{_site(lg)}/scoreboard?dates={ymd}", timeout=15)
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
                "home_id": home["team"].get("id"), "away_id": away["team"].get("id"),
                "home_name": home["team"].get("shortDisplayName"),
                "away_name": away["team"].get("shortDisplayName"),
                "date": e.get("date"), "state": st.get("state"),
                "detail": st.get("shortDetail"),
                "home_score": home.get("score"), "away_score": away.get("score")})
        return out
    return racing._cached(("bk_sched", lg, ymd), 300, build) or []


# ---- Kalshi: ML + spread + total, matched at the listed lines ----------------

def _spread_label(team, m):
    """Kalshi's own wording for a spread leg (combo_engine.spread_label). These
    margins are already half-point LINES, so they pass straight through -- the
    change here is phrasing, so a leg reads the same across every sport."""
    import combo_engine
    return combo_engine.spread_label(team, m, "points")

_SPREAD_RE = re.compile(r"^(.*?)\s+wins by over", re.I)


def _kalshi_index(lg):
    """{suffix: {ml: {city: {cents, ticker, close}}, spreads: [...], totals:
    [...]}} — cached 2m."""
    import kalshi
    cfg = LEAGUES[lg]

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
        for m in fetch(cfg["kx_ml"]):
            city = (m.get("yes_sub_title") or "").strip().lower()
            if not city:
                continue
            ev(m)["ml"][city] = {"cents": _k._cents(m.get("yes_ask_dollars")),
                                 "ticker": m.get("ticker"),
                                 "close": _k._parse_time(m.get("close_time"))}
        for m in fetch(cfg["kx_spread"]):
            t = _SPREAD_RE.match(m.get("yes_sub_title") or m.get("title") or "")
            line = m.get("floor_strike")
            if t and line is not None:
                ev(m)["spreads"].append({"city": t.group(1).strip().lower(),
                                         "line": float(line),
                                         "cents": _k._cents(m.get("yes_ask_dollars"))})
        for m in fetch(cfg["kx_total"]):
            line = m.get("floor_strike")
            if line is not None:
                ev(m)["totals"].append({"line": float(line),
                                        "cents": _k._cents(m.get("yes_ask_dollars"))})
        return idx
    return racing._cached(("bk_kalshi", lg), 120, build) or {}


def _city_match(city, meta):
    return bool(city) and (city.startswith(meta["location"])
                           or meta["location"].startswith(city)
                           or (meta["nick"] and meta["nick"] in city))


def game_markets(lg, home_ab, away_ab):
    """Kalshi's ML/spread/total book for one matchup, keyed to home/away."""
    tmap = teams(lg)
    hm, am = tmap.get(home_ab), tmap.get(away_ab)
    if not hm or not am:
        return None
    for suf, e in _kalshi_index(lg).items():
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


# ---- Possession-level simulator ---------------------------------------------
_FT_POSS = 0.055                     # possessions ending 1 point (splits / and-1)
_PTS_MIX = 0.36 * 2 + 0.115 * 3      # 2pt & 3pt make-rate shape at scale 1


def _poss_probs(eff):
    """(p3, p2, p1) so a possession's mean points ≈ eff."""
    s = max(0.2, min(1.6, (eff - _FT_POSS) / _PTS_MIX))
    return 0.115 * s, 0.36 * s, _FT_POSS


def _run_poss(p3, p2, p1, k, rng):
    pts = 0
    for _ in range(k):
        r = rng.random()
        if r < p3:
            pts += 3
        elif r < p3 + p2:
            pts += 2
        elif r < p3 + p2 + p1:
            pts += 1
    return pts


def quick_game(eff_h, eff_a, pace, rng):
    """ONE possession-level game (the deep-season resolver): returns
    (home_pts, away_pts), never tied. ~200 RNG draws, built for season replays."""
    p3h, p2h, p1h = _poss_probs(eff_h)
    p3a, p2a, p1a = _poss_probs(eff_a)
    poss = max(60, int(round(rng.gauss(pace, 3.4))))
    h = _run_poss(p3h, p2h, p1h, poss, rng)
    a = _run_poss(p3a, p2a, p1a, poss, rng)
    for _ in range(3):
        if h != a:
            break
        h += _run_poss(p3h, p2h, p1h, 8, rng)
        a += _run_poss(p3a, p2a, p1a, 8, rng)
    if h == a:
        if rng.random() < eff_h / (eff_h + eff_a):
            h += 2
        else:
            a += 2
    return h, a


def _shares(plist):
    tot = sum(p["ppg"] for p in plist) or 1.0
    return [(p, p["ppg"] / tot) for p in plist]


def simulate_game(lg, rh, ra, ph_list, pa_list, n=3000, seed=None):
    """Correlated possession MC of one game (full detail: ladders, props, legs)."""
    cfg = LEAGUES[lg]
    rng = random.Random(seed if seed is not None
                        else hash((rh["abbr"], ra["abbr"])) & 0xFFFFFFFF)
    lgp = rh.get("lg_ppp") or cfg["lg_ppp"]
    eff_h = rh["off_ppp"] * (ra["def_ppp"] / lgp) * cfg["hca_eff"]
    eff_a = ra["off_ppp"] * (rh["def_ppp"] / lgp) / cfg["hca_eff"]
    pace = (rh["pace"] + ra["pace"]) / 2.0

    sh_h, sh_a = _shares(ph_list), _shares(pa_list)
    n_ply = len(sh_h) + len(sh_a)
    margins, totals = [], []
    hw = 0
    pts_rows = [[0.0] * n for _ in range(n_ply)]
    reb_rows = [[0.0] * n for _ in range(n_ply)]
    ast_rows = [[0.0] * n for _ in range(n_ply)]

    p3h, p2h, p1h = _poss_probs(eff_h)
    p3a, p2a, p1a = _poss_probs(eff_a)

    for s in range(n):
        poss = max(60, int(round(rng.gauss(pace, 3.4))))
        cut = int(poss * 0.82)
        h_pts = _run_poss(p3h, p2h, p1h, cut, rng)
        a_pts = _run_poss(p3a, p2a, p1a, cut, rng)
        rem = poss - cut
        if abs(h_pts - a_pts) > 14:
            rem = max(6, rem - 2)
            damp = 0.97
        else:
            damp = 1.0
        h_pts += _run_poss(p3h * damp, p2h * damp, p1h, rem, rng)
        a_pts += _run_poss(p3a * damp, p2a * damp, p1a, rem, rng)
        for _ in range(3):
            if h_pts != a_pts:
                break
            h_pts += _run_poss(p3h, p2h, p1h, 8, rng)
            a_pts += _run_poss(p3a, p2a, p1a, 8, rng)
        if h_pts == a_pts:
            if rng.random() < eff_h / (eff_h + eff_a):
                h_pts += 2
            else:
                a_pts += 2

        if h_pts > a_pts:
            hw += 1
        margins.append(h_pts - a_pts)
        totals.append(h_pts + a_pts)
        pace_f = poss / pace

        off = 0
        for team_pts, shares, exp_pts in ((h_pts, sh_h, eff_h * pace),
                                          (a_pts, sh_a, eff_a * pace)):
            perf = team_pts / max(1.0, exp_pts)
            for i, (p, share) in enumerate(shares):
                noise = max(0.05, rng.gauss(1.0, 0.26))
                pts_rows[off + i][s] = team_pts * share * noise
                reb_rows[off + i][s] = p["rpg"] * pace_f * max(0.05, rng.gauss(1.0, 0.33))
                ast_rows[off + i][s] = p["apg"] * perf * max(0.05, rng.gauss(1.0, 0.36))
            off += len(shares)

    p_home = hw / n
    all_ps = [p for p, _ in sh_h] + [p for p, _ in sh_a]
    team_of = [rh["abbr"]] * len(sh_h) + [ra["abbr"]] * len(sh_a)
    mean_total = sum(totals) / n

    def ladder_over(arr, line):
        return sum(1 for x in arr if x > line) / n

    total_ladder = []
    for line in [round(mean_total) + d + 0.5 for d in range(-9, 10, 3)]:
        over = ladder_over(totals, line)
        if 0.05 <= over <= 0.95:
            total_ladder.append({"line": line, "over_pct": round(over * 100, 1)})
    spread_ladder = {"home": {}, "away": {}}
    for m in (1.5, 3.5, 6.5, 9.5, 12.5):
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
             avg=round(sum(margins) / n, 1), unit="pt margin")
    add_cand("ML", f"{an} to win", lambda i: margins[i] < 0, "ML",
             kref={"t": "ml", "team": ra["abbr"]},
             avg=round(-sum(margins) / n, 1), unit="pt margin")
    for m in (3.5, 6.5, 9.5):
        add_cand("Spread", _spread_label(rh["abbr"], m),
                 lambda i, m=m: margins[i] > m, "Spread")
        add_cand("Spread", _spread_label(ra["abbr"], m),
                 lambda i, m=m: margins[i] < -m, "Spread")
    for d in (-5, 0, 5):
        line = round(mean_total) + d + 0.5
        add_cand("Total", f"Over {line}", lambda i, L=line: totals[i] > L, "Total",
                 avg=round(mean_total, 1), unit="points")
        add_cand("Total", f"Under {line}", lambda i, L=line: totals[i] < L, "Total",
                 avg=round(mean_total, 1), unit="points")

    for i, p in enumerate(all_ps):
        arr = pts_rows[i]
        mean = sum(arr) / n
        row = {"name": p["name"], "team": team_of[i], "ppg": p["ppg"],
               "pts": round(mean, 1)}
        srt = sorted(arr)
        row["pts_floor"] = round(srt[int(0.15 * n)], 1)
        row["pts_ceil"] = round(srt[int(0.85 * n)], 1)
        if mean >= 6:
            base = math.floor(mean)
            for line in {base - 3.5, base - 0.5, base + 2.5, base + 5.5}:
                if line < 3:
                    continue
                over = ladder_over(arr, line)
                if 0.12 <= over <= 0.92:
                    props.append({"player": p["name"], "team": team_of[i],
                                  "stat": "points", "line": line,
                                  "over_pct": round(over * 100, 1)})
                if 0.10 <= over <= 0.92:
                    add_cand("Points", f"{p['name']} {line}+ pts",
                             lambda i2, A=arr, L=line: A[i2] > L,
                             f"{p['name']}:pts", avg=round(mean, 1), unit="points")
        for arr2, key, lab, floor_mean in ((reb_rows[i], "reb", "rebounds", 4.5),
                                           (ast_rows[i], "ast", "assists", 3.0)):
            m2 = sum(arr2) / n
            if m2 < floor_mean:
                continue
            row[key] = round(m2, 1)
            line = math.floor(m2) - 0.5 if m2 - math.floor(m2) < 0.5 else math.floor(m2) + 0.5
            over = ladder_over(arr2, line)
            if 0.15 <= over <= 0.9:
                props.append({"player": p["name"], "team": team_of[i],
                              "stat": lab, "line": line,
                              "over_pct": round(over * 100, 1)})
                add_cand(lab.title(), f"{p['name']} {line}+ {lab}",
                         lambda i2, A=arr2, L=line: A[i2] > L,
                         f"{p['name']}:{key}", avg=round(m2, 1), unit=lab)
        players_out.append(row)

    return {"home": rh["abbr"], "away": ra["abbr"],
            "p_home": round(p_home, 4), "p_away": round(1 - p_home, 4),
            "exp_home": round(sum(totals[i] + margins[i] for i in range(n)) / (2 * n), 1),
            "exp_away": round(sum(totals[i] - margins[i] for i in range(n)) / (2 * n), 1),
            "exp_total": round(mean_total, 1),
            "mean_margin": round(sum(margins) / n, 1),
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


def board(lg, date=None):
    """Non-blocking daily slate. Cached 10m (live Kalshi prices refresh).

    Out of season this returns an empty board WITHOUT touching the network:
    there are no games to price, and on a small host the fetch is memory and
    time taken from the sports that are actually playing."""
    try:
        import pro_sim
        if not pro_sim.in_season(lg):
            return {"date": date or clock.today_et().isoformat(), "games": [],
                    "off_season": True, "league": lg}
    except Exception:
        pass
    date = date or clock.today_et().isoformat()
    key = ("bk_board", lg, date)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    if key not in _inflight:
        _inflight.add(key)

        def _bg():
            try:
                val = _build_board(lg, date)
                if val is not None:
                    _cache[key] = (time.time(), val)
            finally:
                _inflight.discard(key)
        threading.Thread(target=_bg, daemon=True).start()
    return hit[1] if hit else None


def _build_board(lg, date, n=3000):
    cfg = LEAGUES[lg]
    sched = schedule(lg, date)
    if not sched:
        return {"date": date, "games": [], "n_games": 0, "league": lg,
                "note": f"No {cfg['label']} games scheduled for this date."}
    rt = ratings(lg)
    pl = players(lg)
    if not rt:
        return None
    try:
        import calibrate
        cal = lambda p: max(0.03, min(0.97, calibrate.apply(cfg["cal"], p)))
    except Exception:
        cal = lambda p: p

    games, log_rows = [], []
    for g in sched:
        rh, ra = rt.get(g["home"]), rt.get(g["away"])
        if not rh or not ra:
            continue
        rh = dict(rh, name=g.get("home_name") or g["home"])
        ra = dict(ra, name=g.get("away_name") or g["away"])
        sim = simulate_game(lg, rh, ra, pl.get(g["home"]) or [],
                            pl.get(g["away"]) or [], n=n)
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
            mk = game_markets(lg, g["home"], g["away"])
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
            row["spread_edges"] = sp_edges[:6]
            row["total_edges"] = tot_edges[:6]
            # Fourth element: the de-vigged price beside the prediction, so
            # vs_market can one day say whether this board would have been PAID,
            # not merely whether it was calibrated.
            _hc = (mk["home_ml"] or {}).get("cents")
            _ac = (mk["away_ml"] or {}).get("cents")
            for side, ml, p, own, opp in (("home", mk["home_ml"], raw_ph, _hc, _ac),
                                          ("away", mk["away_ml"], 1 - raw_ph, _ac, _hc)):
                if ml and ml.get("ticker"):
                    log_rows.append((ml["ticker"], p, ml.get("close"),
                                     predlog.devig(own, opp)))

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
            predlog.init_db()
            predlog.log_many(cfg["cal"], log_rows)
        except Exception:
            pass
    games.sort(key=lambda x: x["date"] or "")
    return {"date": date, "n_games": len(games), "games": games, "n_sims": n,
            "league": lg,
            "note": "Possession-level Monte Carlo: efficiency matchup (points per "
                    "possession vs the opponent's defense) at the game's blended "
                    "pace, with garbage time and OT. Player lines are dealt from "
                    "the simulated team game; spread/total edges are priced at "
                    "Kalshi's exact listed lines."}
