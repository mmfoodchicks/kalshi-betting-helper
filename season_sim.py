"""MLB season Monte Carlo -> our own futures odds (division / playoffs / pennant /
World Series) and season win-total distributions, to compare against Kalshi's
futures markets for edges.

This reuses the per-team strength ratings already built for the game model
(baseball._offense_factor / _pitching_factor / _league_avgs) but deliberately
uses the FAST per-game win-probability (expected runs -> Pythagorean), not the
base-out engine in mlb_sim -- simulating ~1,200 remaining games across thousands
of season-sims with the full lineup engine would be far too slow.

Pipeline:
  current standings (W-L, division, league)         -> baseball standings API
  per-team offense / staff-pitching strength ratings -> baseball rating maps
  remaining schedule (one ranged, fields-limited call)
  N season sims: Bernoulli each remaining game, accumulate wins, seed each
  league's 6-team bracket, play the postseason analytically -> aggregate odds.
"""

import datetime
import random
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from math import comb

import baseball
import kalshi

# Kalshi futures series. Winner-style series have one YES market per team
# (ticker ...-{ABBR}); we map each to one of our simulated probabilities.
_WS_SERIES = ("KXMLBWS", "KXMLBWORLD")          # World Series winner (try both)
_PENNANT = {"KXMLBAL": 103, "KXMLBNL": 104}
_DIVISION = ("KXMLBALEAST", "KXMLBALCENT", "KXMLBALWEST",
             "KXMLBNLEAST", "KXMLBNLCENT", "KXMLBNLWEST")
_PLAYOFFS = "KXMLBPLAYOFFS"
_CONF = {"win_total": "med", "division": "med", "playoffs": "med",
         "pennant": "low", "world_series": "low"}

# MLB postseason: 6 seeds per league. 3 division winners (seeded 1-3 by record)
# + 3 wild cards (4-6). 1&2 bye; WC best-of-3 (3v6, 4v5); DS best-of-5; LCS and
# WS best-of-7.
_DS_NEED, _LCS_NEED, _WS_NEED, _WC_NEED = 3, 4, 4, 2


def _standings(season):
    """team_id -> {name, wins, losses, run_diff, division, league}."""
    data = baseball._get(
        f"{baseball.STATS_BASE}/standings?leagueId=103,104&season={season}")
    out = {}
    for rec in data.get("records", []):
        div = (rec.get("division") or {}).get("id")
        lg = (rec.get("league") or {}).get("id")
        for t in rec.get("teamRecords", []):
            out[t["team"]["id"]] = {
                "name": t["team"]["name"],
                "wins": int(t.get("wins", 0)), "losses": int(t.get("losses", 0)),
                "run_diff": int(t.get("runDifferential", 0)),
                "division": div, "league": lg,
            }
    return out


def _remaining_games(season):
    """List of (home_id, away_id) for every not-yet-final game from today on."""
    start = datetime.date.today().isoformat()
    end = f"{season}-10-05"
    flds = "dates,games,status,abstractGameState,teams,home,away,team,id"
    data = baseball._get(
        f"{baseball.STATS_BASE}/schedule?sportId=1&startDate={start}&endDate={end}&fields={flds}")
    games = []
    for d in data.get("dates", []):
        for g in d.get("games", []):
            if (g.get("status") or {}).get("abstractGameState") == "Final":
                continue
            try:
                games.append((g["teams"]["home"]["team"]["id"],
                              g["teams"]["away"]["team"]["id"]))
            except (KeyError, TypeError):
                pass
    return games


def _strength(season):
    """team_id -> {off, pit} multiplicative factors vs league average, plus lg."""
    hit = baseball._hitting_map(season)
    pit = baseball._pitching_map(season)
    bp = baseball._bullpen_map(season)
    hitplat = baseball._hitting_platoon(season)
    lg = baseball._league_avgs(hit, pit, bp, hitplat)
    era = lg.get("era") or 4.3
    teams = {}
    for tid, th in hit.items():
        # Neutral-hand offense factor (we don't know each game's starter hand).
        off = baseball._offense_factor(th, th.get("ops"), "R", lg)
        staff_era = (pit.get(tid) or {}).get("era") or era
        teams[tid] = {"off": off, "pit": staff_era / era if era else 1.0}
    return teams, lg


def _win_prob(home, away, teams, lg, home_field=True):
    """P(home beats away) from expected runs + Pythagorean exponent."""
    th = teams.get(home, {"off": 1.0, "pit": 1.0})
    ta = teams.get(away, {"off": 1.0, "pit": 1.0})
    rpg = lg.get("rpg") or 4.3
    er_h = rpg * th["off"] * ta["pit"] * (baseball.HOME_RUNS_MULT if home_field else 1.0)
    er_a = rpg * ta["off"] * th["pit"]
    e = baseball.PYTH_EXP
    return er_h ** e / (er_h ** e + er_a ** e)


def _series_p(p, need):
    """P(team with single-game prob p wins a series, first to `need` wins)."""
    total = 0.0
    for losses in range(need):           # opponent wins before we clinch
        total += comb(need - 1 + losses, losses) * (p ** need) * ((1 - p) ** losses)
    return total


def simulate(season=None, n=4000):
    season = season or str(datetime.date.today().year)
    stand = _standings(season)
    teams, lg = _strength(season)
    games = _remaining_games(season)
    # Drop exhibitions / All-Star games (teams not in the standings).
    games = [(h, a) for (h, a) in games if h in stand and a in stand]
    abbr = {}
    try:
        abbr = baseball._abbr_map(season)
    except Exception:
        pass
    tids = list(stand.keys())
    leagues = defaultdict(list)
    divisions = defaultdict(list)
    for tid in tids:
        leagues[stand[tid]["league"]].append(tid)
        divisions[stand[tid]["division"]].append(tid)

    # Pre-compute per-matchup home win prob (regular season has home field).
    pmap = {m: _win_prob(m[0], m[1], teams, lg) for m in set(games)}
    # Neutral single-game probs for the postseason (cache lazily).
    neut = {}

    def npr(a, b):
        if (a, b) not in neut:
            p = _win_prob(a, b, teams, lg, home_field=False)
            neut[(a, b)] = p
            neut[(b, a)] = 1 - p
        return neut[(a, b)]

    rnd = random.random
    win_samples = defaultdict(list)
    playoffs = defaultdict(int)
    div_titles = defaultdict(int)
    pennants = defaultdict(int)
    rings = defaultdict(int)

    for _ in range(n):
        wins = {tid: stand[tid]["wins"] for tid in tids}
        for h, a in games:
            if rnd() < pmap[(h, a)]:
                wins[h] += 1
            else:
                wins[a] += 1
        for tid in tids:
            win_samples[tid].append(wins[tid])

        champs = {}
        for lg_id, members in leagues.items():
            # Rank with a random tiebreaker so ties resolve probabilistically.
            order = sorted(members, key=lambda t: (wins[t], rnd()), reverse=True)
            div_winner = {}
            for tid in order:
                d = stand[tid]["division"]
                if d not in div_winner:
                    div_winner[d] = tid
            dws = sorted(div_winner.values(), key=lambda t: (wins[t], rnd()), reverse=True)
            wcs = [t for t in order if t not in set(dws)][:3]
            seeds = dws + wcs                       # length 6
            for tid in dws:
                div_titles[tid] += 1
            for tid in seeds:
                playoffs[tid] += 1
            # Wild Card round (best-of-3): 3v6, 4v5; seeds 1,2 bye.
            w36 = seeds[2] if rnd() < _series_p(npr(seeds[2], seeds[5]), _WC_NEED) else seeds[5]
            w45 = seeds[3] if rnd() < _series_p(npr(seeds[3], seeds[4]), _WC_NEED) else seeds[4]
            # Division Series (best-of-5): 1 vs lower remaining seed, 2 vs other.
            ds_lo, ds_hi = (w36, w45) if seeds.index(w36) > seeds.index(w45) else (w45, w36)
            d1 = seeds[0] if rnd() < _series_p(npr(seeds[0], ds_lo), _DS_NEED) else ds_lo
            d2 = seeds[1] if rnd() < _series_p(npr(seeds[1], ds_hi), _DS_NEED) else ds_hi
            # LCS (best-of-7) -> pennant.
            champ = d1 if rnd() < _series_p(npr(d1, d2), _LCS_NEED) else d2
            champs[lg_id] = champ
            pennants[champ] += 1
        # World Series (best-of-7).
        lg_ids = list(champs.keys())
        if len(lg_ids) == 2:
            a, b = champs[lg_ids[0]], champs[lg_ids[1]]
            winner = a if rnd() < _series_p(npr(a, b), _WS_NEED) else b
            rings[winner] += 1

    def pct(c):
        return round(100.0 * c / n, 1)

    teams_out = []
    for tid in tids:
        s = sorted(win_samples[tid])
        mean = sum(s) / len(s)
        teams_out.append({
            "team_id": tid, "name": stand[tid]["name"], "abbr": abbr.get(tid),
            "division": stand[tid]["division"], "league": stand[tid]["league"],
            "wins": stand[tid]["wins"], "losses": stand[tid]["losses"],
            "games_left": len(s) and sum(1 for h, a in games if tid in (h, a)),
            "proj_wins": round(mean, 1),
            "proj_p10": s[int(0.10 * (len(s) - 1))],
            "proj_p50": s[int(0.50 * (len(s) - 1))],
            "proj_p90": s[int(0.90 * (len(s) - 1))],
            "p_playoffs": pct(playoffs[tid]), "p_division": pct(div_titles[tid]),
            "p_pennant": pct(pennants[tid]), "p_ws": pct(rings[tid]),
            # full win-total sample retained for ladder pricing (model P(wins > line))
            "_wins_sample": s,
        })
    teams_out.sort(key=lambda t: t["p_ws"], reverse=True)
    return {"season": season, "n_sims": n, "n_games_left": len(games),
            "teams": teams_out}


def _winner_markets(series):
    """[(abbr, market)] for a winner-style futures series (one YES per team)."""
    out = []
    try:
        for m in kalshi.markets_for_series(series, limit=60):
            if m.get("yes_ask") is None:
                continue
            out.append((m["ticker"].rsplit("-", 1)[-1], m))
    except Exception:
        pass
    return out


def _futrow(team, mtype, label, model_pct, kmarket, poly_cents):
    """One futures row priced against Kalshi and/or Polymarket. Edge is vs the
    cheapest book that lists our side (the one we'd actually buy)."""
    kc = kmarket.get("yes_ask") if kmarket else None
    spread = vol = None
    if kmarket:
        vol = kmarket.get("volume") or 0
        if kc is not None and kmarket.get("yes_bid") is not None:
            spread = round(kc - kmarket["yes_bid"], 1)
    books = {b: c for b, c in (("Kalshi", kc), ("Polymarket", poly_cents)) if c}
    best_book = min(books, key=books.get) if books else None
    best = books.get(best_book)
    # Thin only if our sole quote is a wide/untraded Kalshi book.
    thin = (poly_cents is None and (spread is None or spread >= 12 or (vol or 0) < 20))
    return {
        "type": mtype, "team": team["name"], "abbr": team["abbr"], "label": label,
        "model_pct": round(model_pct, 1),
        "kalshi_cents": kc, "poly_cents": poly_cents,
        "market_cents": best, "best_book": best_book,
        "market_payout_x": round(100.0 / best, 2) if best else None,
        "edge": round(model_pct - best, 1) if best is not None else None,
        "edge_kalshi": round(model_pct - kc, 1) if kc is not None else None,
        "edge_poly": round(model_pct - poly_cents, 1) if poly_cents is not None else None,
        "ticker": kmarket.get("ticker") if kmarket else None,
        "volume": vol, "spread": spread, "thin": thin,
        "confidence": _CONF.get(mtype, "med"),
    }


def _kalshi_winner_map(series_iter):
    out = {}
    for series in series_iter:
        for abbr, m in _winner_markets(series):
            out.setdefault(abbr, m)
    return out


def futures_edges(season=None, sim=None, n=4000):
    """Rank our season model vs BOTH books (Kalshi + Polymarket) across World
    Series, pennants, divisions, playoff berths, and per-team win totals. Rows are
    model-driven, so a future shows even when only one book lists it. Edge is vs
    the cheaper book; returns rows sorted by absolute edge + a lean summary."""
    season = season or str(datetime.date.today().year)
    sim = sim or cached(season, n)
    teams = sim["teams"]
    sample = {t["abbr"]: t.get("_wins_sample") or [] for t in teams}

    k_ws = _kalshi_winner_map(_WS_SERIES)
    k_pen = _kalshi_winner_map(_PENNANT.keys())
    k_div = _kalshi_winner_map(_DIVISION)
    k_po = _kalshi_winner_map((_PLAYOFFS,))
    poly = {}
    pm = None
    try:
        import polymarket as pm
        pf = pm.mlb_futures()
        poly = {"world_series": pf.get("world_series", {}),
                "pennant": {**pf.get("al", {}), **pf.get("nl", {})}}
    except Exception:
        pm = None

    rows = []
    cats = [("world_series", "p_ws", k_ws, "win World Series"),
            ("pennant", "p_pennant", k_pen, "win pennant"),
            ("division", "p_division", k_div, "win division"),
            ("playoffs", "p_playoffs", k_po, "make playoffs")]
    for mtype, key, kmap, verb in cats:
        for t in teams:
            model = t.get(key)
            if model is None:
                continue
            pc = pm.match_team(t["name"], poly[mtype]) if (pm and mtype in poly) else None
            km = kmap.get(t["abbr"])
            if km is None and pc is None:
                continue
            rows.append(_futrow(t, mtype, f"{t['name']} {verb}", model, km, pc))

    # Season win totals: one Kalshi series per team, P(final wins >= line).
    def win_total(t):
        s = sample.get(t["abbr"]) or []
        if not s:
            return []
        ladder = []
        for m in _winner_markets_winstotal(t["abbr"]):
            line = m.get("floor")
            if line is None:
                continue
            model = 100.0 * sum(1 for w in s if w >= line) / len(s)
            ladder.append(_futrow(t, "win_total", f"{t['name']} {int(line)}+ wins",
                                  model, m, None))
        return ladder
    with ThreadPoolExecutor(max_workers=10) as ex:
        for ladder in ex.map(win_total, teams):
            rows.extend(ladder)

    # Real (liquid) edges first, biggest gap first; stale/untraded markets sink.
    rows.sort(key=lambda r: (r["thin"], -abs(r["edge"])))
    # Lean summary over LIQUID markets only (preseason win-total books are full of
    # stale wide quotes that would otherwise dominate and mislead).
    summary = {}
    for r in rows:
        if r["thin"]:
            continue
        s = summary.setdefault(r["type"], {"count": 0, "pos": 0, "neg": 0, "edge_sum": 0.0})
        s["count"] += 1
        s["pos" if r["edge"] >= 0 else "neg"] += 1
        s["edge_sum"] += r["edge"]
    for t, s in summary.items():
        s["avg_edge"] = round(s["edge_sum"] / s["count"], 1)
        del s["edge_sum"]
    liquid = sum(1 for r in rows if not r["thin"])
    return {"season": season, "n_sims": sim["n_sims"], "edges": rows,
            "n_liquid": liquid, "summary": summary}


def _winner_markets_winstotal(abbr):
    try:
        return [m for m in kalshi.markets_for_series(f"KXMLBWINS-{abbr}", limit=20)
                if m.get("yes_ask") is not None]
    except Exception:
        return []


def cached(season=None, n=4000, ttl=21600):
    """Daily-ish cached season sim (heavy: ~1,200 games x n sims)."""
    season = season or str(datetime.date.today().year)
    return baseball._cached(("season_sim", season, n), ttl, lambda: simulate(season, n))
