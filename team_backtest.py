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


_SITE = "https://site.api.espn.com/apis/site/v2/sports"
# Rough season windows (start year, months) so the scoreboard sweep covers the
# whole schedule without probing empty dates.
_WINDOWS = {"nhl": (-1, 10, 4), "nba": (-1, 10, 4), "nfl": (-1, 9, 2),
            "mlb": (0, 3, 10)}


def season_games(key, year):
    """EVERY game of a season with its final score — cheap. The scoreboard takes
    a DATE RANGE, so a full schedule is ~20 calls instead of one per game. Teams
    are keyed by abbreviation. Odds are NOT here (ESPN drops them from the site
    feed once a game is final); they're fetched per game in `odds_for`."""
    cfg = LEAGUES[key]
    off, m0, m1 = _WINDOWS.get(key, (0, 1, 12))

    def build():
        import concurrent.futures as cf
        import datetime as _dt
        spans = []
        y0 = year + off
        cur = _dt.date(y0, m0, 1)
        end_y = year if off else year
        end = _dt.date(end_y, m1, 28)
        while cur < end:
            nxt = min(cur + _dt.timedelta(days=10), end)
            spans.append((cur.strftime("%Y%m%d"), nxt.strftime("%Y%m%d")))
            cur = nxt + _dt.timedelta(days=1)

        def one(span):
            a, b = span
            try:
                d = racing._get_json(
                    f"{_SITE}/{cfg['sport']}/{cfg['league']}/scoreboard"
                    f"?dates={a}-{b}&limit=1000", timeout=25)
            except Exception:
                return []
            out = []
            for e in d.get("events") or []:
                c = (e.get("competitions") or [{}])[0]
                st = (((c.get("status") or {}).get("type")) or {}).get("state")
                if st != "post":
                    continue
                cs = c.get("competitors") or []
                if len(cs) != 2:
                    continue
                home = away = None
                hs = as_ = None
                for x in cs:
                    ab = (x.get("team") or {}).get("abbreviation")
                    try:
                        sc = float(x.get("score"))
                    except (TypeError, ValueError):
                        sc = None
                    if x.get("homeAway") == "home":
                        home, hs = ab, sc
                    else:
                        away, as_ = ab, sc
                if home and away and hs is not None and as_ is not None:
                    out.append({"id": e.get("id"), "date": (e.get("date") or "")[:10],
                                "home": home, "away": away, "hs": hs, "as_": as_})
            return out

        rows = []
        with cf.ThreadPoolExecutor(max_workers=6) as ex:
            for got in ex.map(one, spans):
                rows.extend(got)
        seen, uniq = set(), []
        for r in sorted(rows, key=lambda r: r["date"]):
            if r["id"] not in seen:
                seen.add(r["id"])
                uniq.append(r)
        return uniq
    return racing._cached(("btg_season", key, year), 30 * 86400, build) or []


def odds_for(key, event_id):
    """De-vigged closing P(home win) for one game, or None."""
    cfg = LEAGUES[key]
    base = (f"http://sports.core.api.espn.com/v2/sports/{cfg['sport']}"
            f"/leagues/{cfg['league']}/events/{event_id}")

    def build():
        try:
            ev = racing._get_json(base, timeout=15)
            comp = (ev.get("competitions") or [{}])[0]
            o = racing._get_json(comp["$ref"].split("?")[0] + "/odds", timeout=15)
            items = o.get("items") or []
            if not items:
                return None
            # Some leagues return the odds INLINE in the item; others return a
            # $ref to fetch. Blindly dereferencing raised KeyError on the inline
            # form, which the except swallowed — every MLB game came back
            # unpriced and the market column read n=0. Handle both shapes, and
            # try each provider rather than only the first.
            for it in items:
                rec = it
                if "homeTeamOdds" not in rec and it.get("$ref"):
                    try:
                        rec = racing._get_json(it["$ref"], timeout=15)
                    except Exception:
                        continue
                ph = _american_to_prob((rec.get("homeTeamOdds") or {}).get("moneyLine"))
                pa = _american_to_prob((rec.get("awayTeamOdds") or {}).get("moneyLine"))
                if ph and pa and (ph + pa) > 0:
                    return ph / (ph + pa)
        except Exception:
            return None
        return None
    return racing._cached(("btg_odds", key, event_id), 30 * 86400, build)


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


def run(key, year, min_games=8, eval_n=300, sims=300):
    """Walk a season point-in-time and score the model against the market."""
    rows = season_games(key, year)
    if not rows:
        return {"error": f"no completed games for {key} {year}"}
    # Ratings are built from EVERY game of the season, but only an evenly spaced
    # sample is scored — fetching a closing line costs a request per game, while
    # the scores are nearly free. Sampling the evaluation keeps the run tractable
    # WITHOUT starving the model's inputs, which sampling the schedule would.
    step = max(1, len(rows) // max(1, eval_n))
    gf, ga, gp = defaultdict(float), defaultdict(float), defaultdict(int)
    rng = random.Random(7)
    model, market, both = [], [], []
    _K = 4.0                                    # pseudo-games of regression
    for i, g in enumerate(rows):
        h, a = g["home"], g["away"]
        if i % step == 0 and gp[h] >= min_games and gp[a] >= min_games:
            mkt = odds_for(key, g["id"])
            if mkt is not None:
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
                market.append((mkt, y))
                both.append((p, mkt, y))
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
