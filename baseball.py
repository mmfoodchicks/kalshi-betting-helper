"""Baseball (MLB) betting insights + parlay combo generator.

Data sources (all public, no key):
  - MLB Stats API (statsapi.mlb.com): today's schedule, team records,
    runs scored/allowed, abbreviations.
  - Kalshi KXMLBGAME markets: live moneyline-style prices per team.

Model
-----
For each game we estimate each team's true strength, then the probability the
home team wins:

  1. Pythagorean expectation from runs:  pyth = RS^1.83 / (RS^1.83 + RA^1.83)
     -- a truer talent signal than raw win-loss.
  2. Blend with actual win%:  rating = 0.5*winpct + 0.5*pyth
  3. log5 to get a head-to-head probability from the two ratings.
  4. Add home-field advantage (~3.5 percentage points).

Then we pick the favorite per game, and build parlay ("combo") suggestions by
multiplying win probabilities. When live Kalshi prices are available we also
compute the parlay's expected value so you can see which combos are +EV.
"""

import itertools
import math
from datetime import datetime, timezone

import kalshi  # reuse BASE + _get_json + _parse_time helpers

STATS_BASE = "https://statsapi.mlb.com/api/v1"
PYTH_EXP = 1.83          # standard MLB Pythagorean exponent
HOME_FIELD_ADV = 0.035   # ~3.5pp bump to the home team's win probability


def _get(url):
    return kalshi._get_json(url)


def _team_abbr_map(season):
    data = _get(f"{STATS_BASE}/teams?sportId=1&season={season}")
    return {t["id"]: t.get("abbreviation", "") for t in data.get("teams", [])}


def _strength_map(season):
    """team_id -> strength dict from current standings."""
    data = _get(f"{STATS_BASE}/standings?leagueId=103,104&season={season}")
    out = {}
    for rec in data.get("records", []):
        for t in rec.get("teamRecords", []):
            rs = float(t.get("runsScored", 0) or 0)
            ra = float(t.get("runsAllowed", 0) or 0)
            wins = int(t.get("wins", 0) or 0)
            losses = int(t.get("losses", 0) or 0)
            gp = wins + losses
            winpct = wins / gp if gp else 0.5
            if rs > 0 and ra > 0:
                pyth = rs ** PYTH_EXP / (rs ** PYTH_EXP + ra ** PYTH_EXP)
            else:
                pyth = winpct
            rating = 0.5 * winpct + 0.5 * pyth
            out[t["team"]["id"]] = {
                "wins": wins, "losses": losses, "winpct": round(winpct, 3),
                "rs": int(rs), "ra": int(ra),
                "run_diff": int(rs - ra), "pyth": round(pyth, 3),
                "rating": round(rating, 4),
            }
    return out


def _log5(r_home, r_away):
    """log5 probability the home team beats the away team (pre home-field)."""
    denom = r_home + r_away - 2 * r_home * r_away
    if denom <= 0:
        return 0.5
    return (r_home - r_home * r_away) / denom


def get_schedule(date):
    data = _get(f"{STATS_BASE}/schedule?sportId=1&date={date}")
    dates = data.get("dates", [])
    games = dates[0]["games"] if dates else []
    out = []
    for g in games:
        home = g["teams"]["home"]["team"]
        away = g["teams"]["away"]["team"]
        out.append({
            "game_pk": g.get("gamePk"),
            "home_id": home["id"], "home_name": home["name"],
            "away_id": away["id"], "away_name": away["name"],
            "start": g.get("gameDate"),
            "start_epoch": kalshi._parse_time(g.get("gameDate")),
            "status": g.get("status", {}).get("detailedState", ""),
        })
    return out


def get_kalshi_prices():
    """Index live Kalshi MLB game prices by frozenset of team codes.

    Returns: { frozenset({'NYM','PHI'}): {'event': ticker, 'close': epoch,
               'prices': {'NYM': yes_ask_cents, 'PHI': yes_ask_cents}} }
    A series can have the same matchup on multiple days; we keep all and match
    by closest start time later.
    """
    markets = []
    cursor = None
    for _ in range(4):  # a few pages is plenty for a day's slate
        url = "{}/markets?series_ticker=KXMLBGAME&status=open&limit=200".format(kalshi.BASE)
        if cursor:
            url += f"&cursor={cursor}"
        data = _get(url)
        markets.extend(data.get("markets", []))
        cursor = data.get("cursor")
        if not cursor:
            break
    by_event = {}
    for m in markets:
        ev = m.get("event_ticker")
        team = (m.get("ticker", "").rsplit("-", 1) + [""])[1]
        if not ev or not team:
            continue
        entry = by_event.setdefault(ev, {"event": ev, "close": kalshi._parse_time(m.get("close_time")), "prices": {}})
        entry["prices"][team] = kalshi._cents(m.get("yes_ask_dollars"))
    # Re-key by team set, keeping a list for multi-day matchups.
    out = {}
    for ev, e in by_event.items():
        teams = frozenset(e["prices"].keys())
        if len(teams) >= 2:
            out.setdefault(teams, []).append(e)
    return out


def _match_price(kalshi_index, abbr_map, game):
    home = abbr_map.get(game["home_id"], "")
    away = abbr_map.get(game["away_id"], "")
    key = frozenset({home, away})
    candidates = kalshi_index.get(key)
    if not candidates:
        return None, home, away
    # Pick the Kalshi event closest in time to this game's start.
    gstart = game["start_epoch"] or 0
    best = min(candidates, key=lambda e: abs((e["close"] or 0) - gstart))
    return best, home, away


def analyze_slate(date, season):
    """Return per-game model predictions (with Kalshi edge when matchable)."""
    schedule = get_schedule(date)
    strength = _strength_map(season)
    abbr_map = _team_abbr_map(season)
    try:
        kalshi_index = get_kalshi_prices()
    except Exception:
        kalshi_index = {}

    games = []
    for g in schedule:
        sh = strength.get(g["home_id"], {"rating": 0.5})
        sa = strength.get(g["away_id"], {"rating": 0.5})
        p_home = _log5(sh["rating"], sa["rating"]) + HOME_FIELD_ADV
        p_home = max(0.04, min(0.96, p_home))
        p_away = 1 - p_home

        pick_home = p_home >= p_away
        pick_name = g["home_name"] if pick_home else g["away_name"]
        pick_prob = p_home if pick_home else p_away

        price_entry, home_abbr, away_abbr = _match_price(kalshi_index, abbr_map, g)
        edge = None
        market_prob = None
        pick_price = None
        if price_entry:
            pick_abbr = home_abbr if pick_home else away_abbr
            pick_price = price_entry["prices"].get(pick_abbr)
            if pick_price is not None:
                market_prob = round(pick_price, 1)
                edge = round(pick_prob * 100 - pick_price, 1)

        games.append({
            "game_pk": g["game_pk"],
            "matchup": f"{g['away_name']} @ {g['home_name']}",
            "away_name": g["away_name"], "home_name": g["home_name"],
            "away_abbr": away_abbr, "home_abbr": home_abbr,
            "start": g["start"], "status": g["status"],
            "home_strength": sh, "away_strength": sa,
            "p_home": round(p_home, 4), "p_away": round(p_away, 4),
            "pick": pick_name,
            "pick_is_home": pick_home,
            "pick_prob": round(pick_prob, 4),
            "pick_pct": round(pick_prob * 100, 1),
            "confidence": round(abs(pick_prob - 0.5) * 200),  # 0..100
            "pick_price_cents": pick_price,
            "market_prob": market_prob,
            "edge_cents": edge,
        })
    games.sort(key=lambda x: x["pick_prob"], reverse=True)
    return games


def build_combos(games, max_legs=3, top_n=6):
    """Generate parlay combo suggestions from the most confident picks.

    For each combo: combined win probability (product), and -- when every leg
    has a live Kalshi price -- the parlay cost, decimal payout, and EV%.
    Returns combos grouped and the standout 'safest' and 'best value' picks.
    """
    pool = [g for g in games if g["pick_prob"] >= 0.5][:top_n]
    combos = []
    for legs in range(2, max_legs + 1):
        for combo in itertools.combinations(pool, legs):
            prob = 1.0
            cost = 1.0
            priced = True
            for g in combo:
                prob *= g["pick_prob"]
                if g["pick_price_cents"]:
                    cost *= g["pick_price_cents"] / 100.0
                else:
                    priced = False
            item = {
                "legs": [
                    {"pick": g["pick"], "matchup": g["matchup"],
                     "prob_pct": g["pick_pct"], "price_cents": g["pick_price_cents"]}
                    for g in combo
                ],
                "n_legs": legs,
                "combined_prob_pct": round(prob * 100, 1),
                "fair_payout_x": round(1 / prob, 2) if prob > 0 else None,
            }
            if priced and cost > 0:
                payout = 1 / cost           # $1 parlayed -> this much if all hit
                ev_pct = round((prob * payout - 1) * 100, 1)
                item["parlay_payout_x"] = round(payout, 2)
                item["parlay_cost_cents"] = round(cost * 100, 1)
                item["ev_pct"] = ev_pct
            combos.append(item)

    safest = max(combos, key=lambda c: c["combined_prob_pct"], default=None)
    priced_combos = [c for c in combos if c.get("ev_pct") is not None]
    best_value = max(priced_combos, key=lambda c: c["ev_pct"], default=None)
    return {
        "safest": safest,
        "best_value": best_value,
        "all": sorted(combos, key=lambda c: c["combined_prob_pct"], reverse=True)[:12],
    }
