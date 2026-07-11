"""NFL season Monte Carlo -> our own futures odds (Super Bowl / conference /
division / playoffs / win totals), the NFL twin of season_sim.py (MLB).

Inputs:
  ratings      roster-aware projected wins per team (the cached NFL projection
               board) — preseason signal; once real games are played the rating
               blends toward actual points differential, game by game.
  schedule     every regular-season game (home/away/week) from ESPN, with
               completed games LOCKED to their real result — the sim always
               plays only the remaining schedule, exactly like the MLB engine.
  prices       Kalshi NFL futures via the pro_prices series maps.

The board payload mirrors season_sim.futures_board to the key, so the Featured
tab renders both sports with the same UI. Per-team player stat lines come from
Sleeper's weekly projections (preseason) merged with real weekly stats as the
season plays out (headline = actual-to-date + projected remainder; the actual
line rides in `real`, same semantics as the MLB team view).
"""
import math
import random
import threading
import time
from collections import defaultdict

import clock
import racing

_SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
_SLEEPER = "https://api.sleeper.com"

# Divisions are stable; ESPN's flat standings feed doesn't carry them cleanly.
_DIVS = {
    "AFC East": ("BUF", "MIA", "NE", "NYJ"),
    "AFC North": ("BAL", "CIN", "CLE", "PIT"),
    "AFC South": ("HOU", "IND", "JAX", "TEN"),
    "AFC West": ("DEN", "KC", "LAC", "LV"),
    "NFC East": ("DAL", "NYG", "PHI", "WSH"),
    "NFC North": ("CHI", "DET", "GB", "MIN"),
    "NFC South": ("ATL", "CAR", "NO", "TB"),
    "NFC West": ("ARI", "LAR", "SEA", "SF"),
}
_DIV_OF = {ab: d for d, abs_ in _DIVS.items() for ab in abs_}
_CONF_OF = {ab: d.split()[0] for d, abs_ in _DIVS.items() for ab in abs_}
_CANON = {"WAS": "WSH", "JAC": "JAX", "LA": "LAR", "OAK": "LV", "SD": "LAC"}


def _canon(ab):
    return _CANON.get((ab or "").upper(), (ab or "").upper())


def _phi(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _season():
    t = clock.today_et()
    return t.year if t.month >= 3 else t.year - 1


# ---- Schedule (with real results locked in) ---------------------------------
def _week_games(season, week):
    def build():
        try:
            d = racing._get_json(f"{_SITE}/scoreboard?seasontype=2&week={week}&dates={season}",
                                 timeout=15)
        except Exception:
            return None
        out = []
        for e in (d or {}).get("events", []):
            comp = (e.get("competitions") or [{}])[0]
            cs = comp.get("competitors", [])
            home = next((c for c in cs if c.get("homeAway") == "home"), None)
            away = next((c for c in cs if c.get("homeAway") == "away"), None)
            if not home or not away:
                continue
            state = ((comp.get("status") or {}).get("type") or {}).get("state")
            g = {"week": week, "home": _canon(home["team"]["abbreviation"]),
                 "away": _canon(away["team"]["abbreviation"]), "final": state == "post"}
            if g["final"]:
                try:
                    g["home_won"] = float(home.get("score") or 0) > float(away.get("score") or 0)
                except Exception:
                    g["final"] = False
            out.append(g)
        return out
    return racing._cached(("nflss_wk", season, week), 6 * 3600, build) or []


def full_schedule(season):
    games = []
    for w in range(1, 19):
        games.extend(_week_games(season, w))
    return games


# ---- Ratings -----------------------------------------------------------------
def _ratings(season):
    """{abbr: projected-wins-scale rating}. Preseason = the roster-aware
    projection board; in-season it blends toward actual point differential."""
    import nfl_awards
    proj = {}
    try:
        proj = {_canon(k): v for k, v in (nfl_awards._team_wins() or {}).items()}
    except Exception:
        pass
    out = {ab: proj.get(ab, 8.5) for ab in _DIV_OF}
    try:
        import nfl_live
        rt = nfl_live.team_ratings(season) or {}
        for ab, r in rt.items():
            ab = _canon(ab)
            if ab not in out or not r.get("g"):
                continue
            g = r["g"]
            if r["w"] + r["l"] < 1:
                continue
            # Points differential per game -> wins-scale (~2.7 pd/g = elite).
            live = 8.5 + (r["pf_pg"] - r["pa_pg"]) * 2.5
            w = g / (g + 6.0)
            out[ab] = (1 - w) * out[ab] + w * live
    except Exception:
        pass
    return out


def _wp(ra, rb, home_edge=2.1):
    """P(team A beats team B): ~0.9 points of spread per projected win of gap."""
    margin = (ra - rb) * 0.9 + home_edge
    return _phi(margin / 13.2)


# ---- Season Monte Carlo -------------------------------------------------------
def run_season(season=None, n=4000, seed=None):
    season = str(season or _season())
    games = full_schedule(season)
    if not games:
        return None
    R = _ratings(season)
    rng = random.Random(seed)
    # Pre-draw per-game win probs once.
    remaining = [(g["home"], g["away"], _wp(R[g["home"]], R[g["away"]]))
                 for g in games if not g.get("final")
                 and g["home"] in _DIV_OF and g["away"] in _DIV_OF]
    base_w = defaultdict(int)
    base_g = defaultdict(int)
    for g in games:
        if g.get("final"):
            base_w[g["home"] if g["home_won"] else g["away"]] += 1
            base_g[g["home"]] += 1
            base_g[g["away"]] += 1

    div_titles = defaultdict(int)
    conf_champs = defaultdict(int)
    sb = defaultdict(int)
    playoffs = defaultdict(int)
    win_hist = {ab: defaultdict(int) for ab in _DIV_OF}
    confs = {"AFC": [d for d in _DIVS if d.startswith("AFC")],
             "NFC": [d for d in _DIVS if d.startswith("NFC")]}
    rnd = rng.random

    def series(a, b, host):
        """One playoff game; host None = neutral (Super Bowl)."""
        p = _wp(R[a], R[b], 2.1 if host == a else (-2.1 if host == b else 0.0))
        return a if rnd() < p else b

    for _ in range(n):
        wins = dict(base_w)
        for home, away, p in remaining:
            w = home if rnd() < p else away
            wins[w] = wins.get(w, 0) + 1
        for ab in _DIV_OF:
            win_hist[ab][wins.get(ab, 0)] += 1
        finalists = {}
        for conf, divs in confs.items():
            champs, rest = [], []
            for d in divs:
                members = sorted(_DIVS[d], key=lambda ab: (wins.get(ab, 0), rnd()),
                                 reverse=True)
                champs.append(members[0])
                rest.extend(members[1:])
            champs.sort(key=lambda ab: (wins.get(ab, 0), rnd()), reverse=True)
            wc = sorted(rest, key=lambda ab: (wins.get(ab, 0), rnd()), reverse=True)[:3]
            seeds = champs + wc                      # 1-7
            for ab in champs:
                div_titles[ab] += 1
            for ab in seeds:
                playoffs[ab] += 1
            # Wild card: 2v7 3v6 4v5 (higher seed hosts); 1 bye.
            w27 = series(seeds[1], seeds[6], seeds[1])
            w36 = series(seeds[2], seeds[5], seeds[2])
            w45 = series(seeds[3], seeds[4], seeds[3])
            # Divisional: 1 hosts the LOWEST remaining seed; the other two meet.
            alive = sorted((w27, w36, w45), key=seeds.index)
            d1 = series(seeds[0], alive[-1], seeds[0])
            d2 = series(alive[0], alive[1], alive[0])
            hi, lo = (d1, d2) if seeds.index(d1) < seeds.index(d2) else (d2, d1)
            champ = series(hi, lo, hi)
            conf_champs[champ] += 1
            finalists[conf] = champ
        if len(finalists) == 2:
            a, b = finalists["AFC"], finalists["NFC"]
            sb[series(a, b, None)] += 1

    n_left = len(remaining)
    teams = []
    for ab in _DIV_OF:
        hist = win_hist[ab]
        tot = sum(hist.values()) or 1
        mean = sum(w * c for w, c in hist.items()) / tot
        teams.append({
            "team_id": ab, "name": _team_name(ab), "abbr": ab,
            "division": _DIV_OF[ab], "league": _CONF_OF[ab],
            "wins": base_w.get(ab, 0),
            "losses": max(0, base_g.get(ab, 0) - base_w.get(ab, 0)),
            "proj_wins": round(mean, 1),
            "p_playoffs": round(100.0 * playoffs[ab] / n, 1),
            "p_division": round(100.0 * div_titles[ab] / n, 1),
            "p_pennant": round(100.0 * conf_champs[ab] / n, 1),
            "p_ws": round(100.0 * sb[ab] / n, 1),
            "_wins_hist": dict(hist),
        })
    return {"season": season, "n_sims": n, "n_games_left": n_left, "teams": teams}


_NAMES = {}


def _team_name(ab):
    if not _NAMES:
        try:
            import pro_data
            for t in pro_data.teams("nfl") or []:
                _NAMES[_canon(t.get("abbrev") or "")] = t.get("name") or t.get("nick")
        except Exception:
            pass
    return _NAMES.get(ab, ab)


# ---- Kalshi pricing -----------------------------------------------------------
def _price_maps():
    """{market_type: {abbr: cents}} for champ / conference / division from the
    live Kalshi NFL futures series (pro_prices does the team-name matching)."""
    import pro_data
    import pro_prices
    cfg = pro_prices.SERIES.get("nfl", {})
    tmeta = {t["id"]: t for t in pro_data.teams("nfl") or []}
    by_ab = {_canon(t.get("abbrev") or ""): t for t in tmeta.values()}
    amb = pro_prices._ambiguous_locations(tmeta)
    out = {}
    for mtype, key in (("world_series", "champ"), ("pennant", "conf"),
                       ("division", "division")):
        rows = [r for st in cfg.get(key, []) for r in pro_prices._markets(st)]
        m = {}
        if rows:
            for ab, meta in by_ab.items():
                hit = pro_prices._team_match(meta, rows, amb)
                if hit and hit.get("cents") is not None:
                    m[ab] = hit["cents"]
        out[mtype] = m
    # Season win totals: one market per (team, line) — "9+ wins" in the sub.
    import re
    wt = {}
    win_rows = [r for st in cfg.get("wins", []) for r in pro_prices._markets(st)]
    if win_rows:
        for ab, meta in by_ab.items():
            loc = (meta.get("location") or "").lower()
            nick = (meta.get("nick") or "").lower()
            pin = f"{loc} {nick[:1]}" if loc in amb else loc
            for r in win_rows:
                hay = r["title"] + " " + r["sub"]
                if not ((loc and pin in hay) or (nick and len(nick) > 3 and nick in hay)):
                    continue
                mline = re.search(r"(\d+)\s*\+\s*wins", r["sub"] or "")
                if mline and r.get("cents") is not None:
                    wt[(ab, int(mline.group(1)))] = r["cents"]
    out["win_total"] = wt
    return out


def futures_board(season=None, n=4000):
    """Payload mirrors season_sim.futures_board so the Featured tab reuses the
    exact same market-dropdown + ranked-table UI."""
    sim = _cached_sim(season, n)
    if not sim:
        return None
    ns = sim["n_sims"]
    try:
        prices = _price_maps()
    except Exception:
        prices = {}

    def enrich(t, mtype, pct, line=None):
        km = ((prices.get("win_total") or {}).get((t["abbr"], line)) if line is not None
              else (prices.get(mtype) or {}).get(t["abbr"]))
        return {"team": t["name"], "abbr": t["abbr"], "division": t["division"],
                "league": t["league"], "proj_wins": t["proj_wins"],
                "wins": t["wins"], "losses": t["losses"],
                "count": round(pct / 100.0 * ns), "n": ns,
                "model_pct": round(pct, 1),
                "kalshi_cents": km, "poly_cents": None,
                "best_book": "Kalshi" if km is not None else None,
                "edge": round(pct - km, 1) if km is not None else None,
                "thin": km is None}

    markets, order = {}, []
    for mtype, key, label in (("world_series", "p_ws", "Super Bowl champion"),
                              ("pennant", "p_pennant", "Conference champion (AFC/NFC)"),
                              ("division", "p_division", "Division winner"),
                              ("playoffs", "p_playoffs", "Make the playoffs")):
        rows = [enrich(t, mtype, t[key]) for t in sim["teams"]]
        rows.sort(key=lambda r: r["model_pct"], reverse=True)
        markets[mtype] = {"label": label, "group": "Titles", "teams": rows}
        order.append(mtype)

    # Win-total ladders (model-only until Kalshi's team lines are wired):
    # keep the lines that actually discriminate, like the MLB board.
    for L in range(6, 14):
        rows = []
        for t in sim["teams"]:
            hist = t.get("_wins_hist") or {}
            tot = sum(hist.values()) or 1
            pct = 100.0 * sum(c for w, c in hist.items() if w >= L) / tot
            rows.append(enrich(t, "win_total", pct, line=L))
        if sum(1 for r in rows if 5 <= r["model_pct"] <= 95) < 4:
            continue
        rows.sort(key=lambda r: r["model_pct"], reverse=True)
        key = f"win_{L}"
        markets[key] = {"label": f"{L}+ wins", "group": "Season win totals", "teams": rows}
        order.append(key)

    return {"season": sim["season"], "n_sims": ns, "n_games_left": sim["n_games_left"],
            "engine": "roster", "sport": "nfl", "markets": markets, "order": order}


_SIM_CACHE = {"key": None, "t": 0.0, "sim": None}
_LOCK = threading.Lock()


def _cached_sim(season, n):
    season = str(season or _season())
    with _LOCK:
        fresh = (_SIM_CACHE["key"] == (season, n)
                 and time.time() - _SIM_CACHE["t"] < 6 * 3600)
        if fresh:
            return _SIM_CACHE["sim"]
    # Nightly-scheduler run (persisted to disk) serves first — same cadence as
    # every other season sim; a cold request only computes if that's missing.
    try:
        import deep_cache
        payload, ts = deep_cache.load("nfl_season")
        if (payload and payload.get("season") == season
                and time.time() - (ts or 0) < 26 * 3600):
            with _LOCK:
                _SIM_CACHE.update(key=(season, n), t=time.time(), sim=payload)
            return payload
    except Exception:
        pass
    sim = run_season(season, n)
    with _LOCK:
        if sim:
            _SIM_CACHE.update(key=(season, n), t=time.time(), sim=sim)
    return sim


# ---- Per-team player stat lines (Sleeper proj + real weekly stats) ------------
_POS = {"QB", "RB", "WR", "TE"}
_STAT_KEYS = ("pass_yd", "pass_td", "pass_int", "rush_yd", "rush_td",
              "rec", "rec_yd", "rec_td")


def _sleeper_week(kind, season, week):
    """kind: 'projections' | 'stats'. -> [{player_id, stats, player}, ...]"""
    def build():
        try:
            return racing._get_json(
                f"{_SLEEPER}/{kind}/nfl/{season}/{week}?season_type=regular",
                timeout=20) or []
        except Exception:
            return None
    ttl = 24 * 3600 if kind == "projections" else 6 * 3600
    return racing._cached(("nflss", kind, season, week), ttl, build) or []


def team_detail(abbr, season=None):
    """Per-player projected SEASON stat lines for one team: headline = real
    weeks already played + Sleeper-projected remainder; the real-to-date line
    rides in `real` (the parenthetical), mirroring the MLB team view. Preseason
    the headline is the pure Sleeper season projection."""
    season = str(season or _season())
    ab = _canon(abbr)
    played = {g["week"] for g in full_schedule(season) if g.get("final")}

    def accum(kind, weeks):
        out = defaultdict(lambda: {k: 0.0 for k in _STAT_KEYS})
        names = {}
        for w in weeks:
            for row in _sleeper_week(kind, season, w):
                pl = row.get("player") or {}
                if (pl.get("position") not in _POS
                        or _canon(row.get("team") or pl.get("team") or "") != ab):
                    continue
                pid = row.get("player_id")
                st = row.get("stats") or {}
                a = out[pid]
                for k in _STAT_KEYS:
                    a[k] += st.get(k) or 0.0
                names[pid] = {"name": (pl.get("first_name", "") + " " + pl.get("last_name", "")).strip(),
                              "pos": pl.get("position")}
        return out, names

    all_weeks = list(range(1, 19))
    proj_weeks = [w for w in all_weeks if w not in played]
    proj, names_p = accum("projections", proj_weeks)
    real, names_r = accum("stats", sorted(played)) if played else ({}, {})
    names = {**names_p, **names_r}

    def dk_season(t):
        return round(0.04 * t["pass_yd"] + 4 * t["pass_td"] - t["pass_int"]
                     + 0.1 * t["rush_yd"] + 6 * t["rush_td"]
                     + t["rec"] + 0.1 * t["rec_yd"] + 6 * t["rec_td"], 1)

    rows = []
    for pid, nm in names.items():
        p = proj.get(pid) or {k: 0.0 for k in _STAT_KEYS}
        r = real.get(pid)
        tot = {k: round((r[k] if r else 0.0) + p[k]) for k in _STAT_KEYS}
        if sum(tot.values()) < 5:              # camp bodies with no projection
            continue
        row = {"name": nm["name"], "pos": nm["pos"], **tot, "fpts": dk_season(
            {k: (r[k] if r else 0.0) + p[k] for k in _STAT_KEYS})}
        if r:
            row["real"] = {k: round(v) for k, v in r.items()}
        rows.append(row)
    rows.sort(key=lambda x: -x["fpts"])
    return {"team": _team_name(ab), "abbr": ab, "season": season,
            "weeks_played": len(played),
            "source": "Sleeper projections" + (" + real stats to date" if played else ""),
            "players": rows[:24]}
