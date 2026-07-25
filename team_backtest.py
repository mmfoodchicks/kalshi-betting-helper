"""Point-in-time backtest for the TEAM-sport models — the generic sibling of
ufc_backtest (and distinct from backtest.py, which replays the crypto model).

Same question, asked of every league we model: does our engine actually beat the
closing price, and by how much? That answer should set how far a board departs
from the market, instead of a hand-picked constant.

Method, per league:
  1. Pull a season's games from ESPN's core API — date, teams, final score, and
     the closing moneyline (every league exposes /odds on a competition).
  2. Walk the season in date order. Each game is predicted from ONLY the games
     already played before it (scored for/against per game, regressed toward the
     league mean while the sample is thin). This is the point-in-time discipline:
     no game may see itself or its future, or the backtest measures hindsight.
  3. Predict with the SPORT'S OWN ENGINE — hockey.quick_game (with its overtime),
     basket.quick_game (possession-level) — so we score the real model rather
     than a stand-in.
  4. Score model and de-vigged market on accuracy / Brier / log-loss, and fit the
     blend weight w in w*model + (1-w)*market that minimises log-loss.

Early-season games are skipped (`min_games`): a team with two results carries no
signal and would only add noise to both sides of the comparison.
"""

import math
import random
from collections import defaultdict

import racing

LEAGUES = {
    "nba":  {"sport": "basketball", "league": "nba",  "family": "basketball", "hca": 1.010, "pace": 99.0},
    "wnba": {"sport": "basketball", "league": "wnba", "family": "basketball", "hca": 1.013, "pace": 79.0},
    "nhl":  {"sport": "hockey",     "league": "nhl",  "family": "hockey",     "hca": 1.045},
    "nfl":  {"sport": "football",   "league": "nfl",  "family": "football",   "hca": 2.0},
    "mlb":  {"sport": "baseball",   "league": "mlb",  "family": "runs",       "hca": 0.15},
}


def _american_to_prob(ml):
    try:
        ml = float(ml)
    except (TypeError, ValueError):
        return None
    if ml == 0:
        return None
    return (-ml) / ((-ml) + 100.0) if ml < 0 else 100.0 / (ml + 100.0)


def games(key, year):
    """[{date, home, away, hs, as_, mkt_home}] for a season, chronological.
    mkt_home is the de-vigged closing probability that the HOME side wins."""
    cfg = LEAGUES[key]
    base = (f"http://sports.core.api.espn.com/v2/sports/{cfg['sport']}"
            f"/leagues/{cfg['league']}")

    def build():
        import concurrent.futures as cf
        try:
            d = racing._get_json(f"{base}/events?limit=1000&dates={year}", timeout=30)
        except Exception:
            return None
        refs = [i["$ref"] for i in (d.get("items") or []) if i.get("$ref")]

        def one(ref):
            try:
                ev = racing._get_json(ref, timeout=20)
            except Exception:
                return None
            c = (ev.get("competitions") or [{}])[0]
            date = (c.get("date") or ev.get("date") or "")[:10]
            cs = c.get("competitors") or []
            if len(cs) != 2 or not date:
                return None
            home = away = None
            hs = as_ = None
            for x in cs:
                tid = str((x.get("team") or {}).get("$ref", "")).split("/teams/")[-1].split("?")[0]
                sc = x.get("score")
                if isinstance(sc, dict) and sc.get("$ref"):
                    try:
                        sc = racing._get_json(sc["$ref"], timeout=15).get("value")
                    except Exception:
                        sc = None
                elif isinstance(sc, dict):
                    sc = sc.get("value")
                if x.get("homeAway") == "home":
                    home, hs = tid, sc
                else:
                    away, as_ = tid, sc
            if not home or not away or hs is None or as_ is None:
                return None
            mkt_home = None
            try:
                o = racing._get_json(c["$ref"].split("?")[0] + "/odds", timeout=15)
                items = o.get("items") or []
                if items:
                    rec = racing._get_json(items[0]["$ref"], timeout=15)
                    ph = _american_to_prob((rec.get("homeTeamOdds") or {}).get("moneyLine"))
                    pa = _american_to_prob((rec.get("awayTeamOdds") or {}).get("moneyLine"))
                    if ph and pa and (ph + pa) > 0:
                        mkt_home = ph / (ph + pa)
            except Exception:
                pass
            return {"date": date, "home": home, "away": away,
                    "hs": float(hs), "as_": float(as_), "mkt_home": mkt_home}

        rows = []
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            for g in ex.map(one, refs):
                if g:
                    rows.append(g)
        rows.sort(key=lambda r: r["date"])
        return rows
    return racing._cached(("btg", key, year), 30 * 86400, build) or []


def _predict(key, rh, ra, rng, n=300):
    """Model P(home wins) from point-in-time per-game scoring rates, run through
    the sport's real engine where it has one."""
    cfg = LEAGUES[key]
    fam = cfg["family"]
    lg = rh["lg_for"] or 1.0
    if fam == "hockey":
        import hockey
        exp_h = rh["gf"] * (ra["ga"] / lg) * cfg["hca"]
        exp_a = ra["gf"] * (rh["ga"] / lg) / cfg["hca"]
        w = 0
        for _ in range(n):
            h, a, _ot = hockey.quick_game(exp_h, exp_a, rng)
            w += 1 if h > a else 0
        return w / float(n)
    if fam == "basketball":
        import basket
        pace = cfg.get("pace") or 99.0
        # points/game -> points/possession, opponent-adjusted around the league
        eff_h = (rh["gf"] / pace) * ((ra["ga"] / lg)) * cfg["hca"]
        eff_a = (ra["gf"] / pace) * ((rh["ga"] / lg)) / cfg["hca"]
        w = 0
        for _ in range(n):
            h, a = basket.quick_game(eff_h, eff_a, pace, rng)
            w += 1 if h > a else 0
        return w / float(n)
    # football / baseball: scoring-margin differential through a logistic
    margin = (rh["gf"] - rh["ga"]) - (ra["gf"] - ra["ga"]) + cfg["hca"]
    scale = 9.0 if fam == "football" else 2.6
    return 1.0 / (1.0 + math.exp(-margin / scale))


def _metrics(pairs):
    n = len(pairs)
    if not n:
        return 0, None, None, None
    acc = sum(1 for p, y in pairs if (p >= 0.5) == bool(y)) / n
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    ll = -sum(math.log(max(1e-9, p if y else 1 - p)) for p, y in pairs) / n
    return n, round(acc, 4), round(brier, 4), round(ll, 4)


def run(key, year, min_games=8, limit=None, sims=300):
    """Walk a season point-in-time and score the model against the market."""
    rows = [g for g in games(key, year) if g["mkt_home"] is not None]
    if not rows:
        return {"error": f"no games with closing odds for {key} {year}"}
    if limit:
        rows = rows[:limit]
    gf, ga, gp = defaultdict(float), defaultdict(float), defaultdict(int)
    rng = random.Random(7)
    model, market, both = [], [], []
    _K = 4.0                                    # pseudo-games of regression
    for g in rows:
        h, a = g["home"], g["away"]
        if gp[h] >= min_games and gp[a] >= min_games:
            tot_g = sum(gp.values()) or 1
            lg_for = sum(gf.values()) / tot_g or 1.0

            def rate(t):
                nn = gp[t]
                return {"gf": (gf[t] + _K * lg_for) / (nn + _K),
                        "ga": (ga[t] + _K * lg_for) / (nn + _K),
                        "lg_for": lg_for}
            p = max(0.02, min(0.98, _predict(key, rate(h), rate(a), rng, sims)))
            y = 1 if g["hs"] > g["as_"] else 0
            model.append((p, y))
            market.append((g["mkt_home"], y))
            both.append((p, g["mkt_home"], y))
        gf[h] += g["hs"]; ga[h] += g["as_"]; gp[h] += 1
        gf[a] += g["as_"]; ga[a] += g["hs"]; gp[a] += 1
    out = {"league": key, "year": year, "games_scored": len(model),
           "model": dict(zip(("n", "acc", "brier", "logloss"), _metrics(model))),
           "market": dict(zip(("n", "acc", "brier", "logloss"), _metrics(market)))}
    if both:
        best_w, best_ll = 0.0, None
        for i in range(21):
            w = i / 20.0
            blend = [(max(1e-6, min(1 - 1e-6, w * m + (1 - w) * k)), y) for m, k, y in both]
            _n, _a, _b, ll = _metrics(blend)
            if best_ll is None or ll < best_ll:
                best_w, best_ll = w, ll
        out["best_blend_weight"] = best_w
        out["best_blend_logloss"] = best_ll
        out["verdict"] = ("model adds nothing over the market" if best_w <= 0.05 else
                          "model adds a little" if best_w <= 0.35 else
                          "model carries real independent signal")
    return out
