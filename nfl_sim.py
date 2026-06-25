"""Madden-style NFL season simulator for fantasy / DFS.

Like Madden's franchise sim (and the MLB deep engine), this doesn't run literal
play-by-play — it drives off each player's production 'ratings' (here: their
recency-weighted multi-year stat line) and Monte-Carlos a season game by game.
Per game it samples yardage with realistic spread and touchdowns as lumpy
(Poisson) events — which is what gives fantasy its boom/bust shape — then scores
it in DraftKings points. Out of many simulated seasons we get each player's
per-game fantasy distribution (proj / floor / ceiling) and full-season total: the
projection layer DFS lineups are built on, and a season-long value board for
best-ball drafts in the meantime.
"""

import math
import random

import nfl_awards

GAMES = 17

# DraftKings NFL scoring.
def _dk(pass_yds, pass_td, ints, rush_yds, rush_td, rec, rec_yds, rec_td):
    pts = (0.04 * pass_yds + 4 * pass_td - 1 * ints
           + 0.1 * rush_yds + 6 * rush_td
           + 0.1 * rec_yds + 6 * rec_td + 1.0 * rec)            # full PPR
    if pass_yds >= 300:
        pts += 3
    if rush_yds >= 100:
        pts += 3
    if rec_yds >= 100:
        pts += 3
    return pts


def _pois(lam, rng):
    if lam <= 0:
        return 0
    L = math.exp(-min(30, lam)); k = 0; p = 1.0
    while True:
        k += 1; p *= rng.random()
        if p <= L:
            return k - 1


def _game(pg, rng):
    """One game's DK points for a player from per-game expected components.
    Yardage is sampled with position-typical spread; TDs are lumpy Poisson events
    (the source of fantasy ceilings)."""
    def yd(mean, cv):
        return max(0.0, rng.gauss(mean, mean * cv)) if mean > 0 else 0.0
    pass_yds = yd(pg["pass_yds"], 0.28)
    rush_yds = yd(pg["rush_yds"], 0.55)
    rec_yds = yd(pg["rec_yds"], 0.60)
    rec = max(0, round(rng.gauss(pg["rec"], pg["rec"] * 0.45))) if pg["rec"] > 0 else 0
    return _dk(pass_yds, _pois(pg["pass_td"], rng), _pois(pg["int"], rng),
               rush_yds, _pois(pg["rush_td"], rng),
               rec, rec_yds, _pois(pg["rec_td"], rng))


_FANTASY_POS = {"QB", "RB", "WR", "TE"}
_DRAFT = "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl"

# Expected rookie fantasy points/game by draft slot (peak * exp(-pick/scale) +
# floor), calibrated to how rookie production actually breaks down by where a
# player is taken. Draft capital is the single best predictor of rookie touches.
_ROOK = {"RB": (13.0, 45, 1.5), "WR": (11.0, 55, 1.5),
         "TE": (8.0, 35, 0.8), "QB": (15.0, 22, 1.0)}


def _rookie_fppg(pos, pick):
    peak, scale, floor = _ROOK[pos]
    val = peak * math.exp(-pick / scale) + floor
    if pos == "QB" and pick > 64:        # late QBs almost never start as rookies
        val = min(val, 3.0)
    return val


def rookies(season):
    """Skill-position rookies from this year's draft as
    [{name, pos, team_abbr, pick, base_fppg}]. Cached a month (the draft is set)."""
    def build():
        import pro_data
        teams = {t["id"]: t for t in pro_data.teams("nfl")}
        try:
            rd = nfl_awards._get(f"{_DRAFT}/seasons/{season}/draft/rounds?lang=en")
        except Exception:
            return []
        picks = []
        for r in rd.get("items", [])[:5]:            # rounds 1-5 cover fantasy rookies
            pk = r.get("picks")
            if isinstance(pk, dict):
                try:
                    pk = nfl_awards._get(pk["$ref"]).get("items", [])
                except Exception:
                    pk = []
            picks += pk or []

        def resolve(pk):
            a_ref = (pk.get("athlete") or {}).get("$ref")
            if not a_ref:
                return None
            try:
                a = nfl_awards._get(a_ref)
            except Exception:
                return None
            pos = (a.get("position") or {}).get("abbreviation", "")
            if pos not in _FANTASY_POS:
                return None
            tref = ((pk.get("team") or {}).get("$ref") or "")
            tid = tref.split("/teams/")[-1].split("?")[0] if tref else None
            overall = pk.get("overall") or 260
            return {"name": a.get("displayName"), "pos": pos,
                    "team_abbr": (teams.get(tid) or {}).get("abbrev"),
                    "pick": overall, "base_fppg": _rookie_fppg(pos, overall)}
        import concurrent.futures as cf
        out = []
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            for r in ex.map(resolve, picks):
                if r and r["name"]:
                    out.append(r)
        return out
    return nfl_awards.racing._cached(("nfl_rookies", season), 30 * 86400, build) or []


def project(prior_season=None, n_seasons=4000, seed=None):
    """Per-player fantasy projection board from the season Monte Carlo."""
    import datetime
    prior_season = prior_season or datetime.date.today().year - 1
    cands = nfl_awards.candidates(prior_season)
    if not cands:
        return None
    rng = random.Random(seed)
    rows = []
    for pid, p in cands.items():
        if p["pos"] not in _FANTASY_POS:
            continue
        # multi-year production -> regressed projection, then per-game expectation
        proj = nfl_awards._proj(p["stats"])
        pg = {k: proj.get(k, 0) / GAMES for k in
              ("pass_yds", "pass_td", "int", "rush_yds", "rush_td", "rec_yds", "rec_td", "rec")}
        pg["int"] = proj.get("pass_yds", 0) / GAMES * 0.0007 if proj.get("pass_yds") else 0  # ~ INT rate
        per_game, totals = [], []
        for _ in range(n_seasons):
            games = int(round(GAMES * max(0.3, min(1.0, rng.gauss(0.92, 0.13)))))  # injury
            form = math.exp(rng.gauss(0.0, 0.12))                                  # season form
            season = 0.0
            for _g in range(games):
                fp = _game({k: v * form for k, v in pg.items()}, rng)
                per_game.append(fp)
                season += fp
            totals.append(season)
        per_game.sort(); totals.sort()
        n = len(per_game)
        q = lambda a, f: a[min(len(a) - 1, int(f * len(a)))]
        rows.append({
            "id": pid, "name": p["name"], "pos": p["pos"], "team": p["team_abbr"],
            "fppg": round(sum(per_game) / n, 1),
            "floor": round(q(per_game, 0.25), 1), "ceiling": round(q(per_game, 0.85), 1),
            "boom": round(q(per_game, 0.97), 1),               # smash-game upside (GPP)
            "season": round(sum(totals) / len(totals), 1), "rookie": False,
        })

    # Rookies: no NFL stats, so projected from draft capital + landing spot, with
    # high variance (boom/bust) — exactly the best-ball dart-throw profile.
    tw = nfl_awards._team_wins()
    for rk in rookies(prior_season + 1):
        if not rk["team_abbr"]:
            continue
        tf = max(0.6, 0.85 + 0.04 * (tw.get(rk["team_abbr"], 8.5) - 8))
        mean = rk["base_fppg"] * tf
        per_game, totals = [], []
        for _ in range(n_seasons):
            games = int(round(GAMES * max(0.25, min(1.0, rng.gauss(0.85, 0.18)))))
            season = 0.0
            for _g in range(games):
                fp = max(0.0, rng.gauss(mean, mean * 0.62))    # rookie boom/bust
                per_game.append(fp); season += fp
            totals.append(season)
        per_game.sort(); totals.sort()
        q = lambda a, f: a[min(len(a) - 1, int(f * len(a)))]
        rows.append({
            "id": "rk_" + (rk["name"] or ""), "name": rk["name"], "pos": rk["pos"],
            "team": rk["team_abbr"], "fppg": round(sum(per_game) / len(per_game), 1),
            "floor": round(q(per_game, 0.25), 1), "ceiling": round(q(per_game, 0.85), 1),
            "boom": round(q(per_game, 0.97), 1),
            "season": round(sum(totals) / len(totals), 1),
            "rookie": True, "pick": rk["pick"],
        })
    rows.sort(key=lambda r: -r["fppg"])
    by_pos = {}
    for pos in _FANTASY_POS:
        by_pos[pos] = [r for r in rows if r["pos"] == pos][:30]
    return {"sport": "nfl_dfs", "season": prior_season + 1, "n_seasons": n_seasons,
            "overall": rows[:40], "by_pos": by_pos}
