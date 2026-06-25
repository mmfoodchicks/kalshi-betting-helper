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
            "season": round(sum(totals) / len(totals), 1),
        })
    rows.sort(key=lambda r: -r["fppg"])
    by_pos = {}
    for pos in _FANTASY_POS:
        by_pos[pos] = [r for r in rows if r["pos"] == pos][:30]
    return {"sport": "nfl_dfs", "season": prior_season + 1, "n_seasons": n_seasons,
            "overall": rows[:40], "by_pos": by_pos}
