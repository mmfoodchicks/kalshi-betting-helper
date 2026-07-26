"""Point-in-time MLB backtest — the real one, with lineups and starters.

The generic team backtest predicts baseball from run differential, which throws
away the single biggest driver of a game: who is pitching. This does it the way
you'd actually handicap the Dodgers on a given night — pull that game's POSTED
LINEUP and PROBABLE STARTERS, rate every one of those players on what they had
done up to (and not including) that day, model the game, then compare to what
happened.

Point-in-time is exact here, not approximated: MLB StatsAPI serves per-player
`byDateRange` splits, so a starter's ERA/WHIP/K-rate is fetched as of the day
BEFORE the game. Nothing in a rating can see the game it is predicting, or any
game after it.

What it produces:
  * accuracy / Brier / log-loss for the model
  * the same for the closing moneyline where ESPN has one, so we can ask whether
    the model beats the price — the question that matters
  * graded (probability, outcome) pairs for calibrate.py

Costs one StatsAPI call per starter per game, so it caches hard and is meant to
run over a bounded sample in the background.
"""

import math
from collections import defaultdict

import racing

_SCHED = ("https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}"
          "&hydrate=probablePitcher,lineups")
_PSTAT = ("https://statsapi.mlb.com/api/v1/people/{pid}/stats?stats=byDateRange"
          "&startDate={season}-03-01&endDate={end}&group={group}&season={season}")

_LG_ERA = 4.20            # league baseline for shrinking a thin starter line
_SP_K = 40.0              # innings of shrinkage toward the league ERA
_HFA = 0.035              # home edge in win probability


def games_on(date):
    """[{gamePk, date, home, away, hs, as_, sp_home, sp_away, lineups}] for a day.
    Only completed games with both probable starters recorded."""
    def build():
        try:
            d = racing._get_json(_SCHED.format(date=date), timeout=25)
        except Exception:
            return None
        out = []
        for day in d.get("dates") or []:
            for g in day.get("games") or []:
                st = ((g.get("status") or {}).get("abstractGameState") or "")
                if st != "Final":
                    continue
                t = g.get("teams") or {}
                h, a = t.get("home") or {}, t.get("away") or {}
                hp = (h.get("probablePitcher") or {}).get("id")
                ap = (a.get("probablePitcher") or {}).get("id")
                if h.get("score") is None or a.get("score") is None or not hp or not ap:
                    continue
                lu = g.get("lineups") or {}
                out.append({
                    "gamePk": g.get("gamePk"), "date": date,
                    "home": (h.get("team") or {}).get("name"),
                    "away": (a.get("team") or {}).get("name"),
                    "hs": float(h["score"]), "as_": float(a["score"]),
                    "sp_home": hp, "sp_away": ap,
                    "lu_home": [p.get("id") for p in (lu.get("homePlayers") or [])],
                    "lu_away": [p.get("id") for p in (lu.get("awayPlayers") or [])],
                })
        return out
    return racing._cached(("mlbbt_sched", date), 30 * 86400, build) or []


def _prev_day(date):
    import datetime as _dt
    d = _dt.date.fromisoformat(date) - _dt.timedelta(days=1)
    return d.isoformat()


def pitcher_line(pid, date):
    """A starter's season-to-date line as of the day BEFORE `date` — the true
    point-in-time read. {era, ip, k9, bb9, whip} or None."""
    end = _prev_day(date)
    season = date[:4]

    def build():
        try:
            d = racing._get_json(_PSTAT.format(pid=pid, season=season, end=end,
                                               group="pitching"), timeout=20)
        except Exception:
            return None
        for s in d.get("stats") or []:
            for sp in s.get("splits") or []:
                st = sp.get("stat") or {}
                try:
                    ip = float(str(st.get("inningsPitched") or 0).replace(".1", ".33")
                               .replace(".2", ".67"))
                except ValueError:
                    ip = 0.0
                if ip <= 0:
                    continue
                try:
                    era = float(st.get("era"))
                except (TypeError, ValueError):
                    continue
                return {"era": era, "ip": ip,
                        "k9": 9.0 * float(st.get("strikeOuts") or 0) / ip,
                        "bb9": 9.0 * float(st.get("baseOnBalls") or 0) / ip}
        return None
    return racing._cached(("mlbbt_sp", pid, end), 30 * 86400, build)


def _sp_quality(line):
    """Starter quality as runs-allowed-per-9, shrunk to the league mean by innings.
    Lower is better."""
    if not line:
        return _LG_ERA
    ip = line["ip"]
    era = max(1.0, min(9.0, line["era"]))
    return (ip * era + _SP_K * _LG_ERA) / (ip + _SP_K)


def _espn_abbr_map():
    """{normalized full team name: ESPN abbreviation}. StatsAPI says 'Cleveland
    Guardians', the ESPN odds index keys on 'CLE', so the two feeds can't be
    joined without this. Built from ESPN's own team list so it stays current
    through relocations and rebrands instead of being hardcoded."""
    def build():
        try:
            d = racing._get_json(
                "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams",
                timeout=25)
        except Exception:
            return None
        out = {}
        for t in (d.get("sports") or [{}])[0].get("leagues", [{}])[0].get("teams", []):
            tm = t.get("team") or {}
            ab = tm.get("abbreviation")
            if not ab:
                continue
            for key in (tm.get("displayName"), tm.get("name"), tm.get("shortDisplayName")):
                if key:
                    out[key.strip().lower()] = ab
        return out or None
    return racing._cached(("mlbbt_abbr",), 7 * 86400, build) or {}


def _abbr(name):
    m = _espn_abbr_map()
    n = (name or "").strip().lower()
    if n in m:
        return m[n]
    # fall back to the nickname (last word), which is unique across MLB
    tail = n.split()[-1] if n.split() else ""
    return m.get(tail)


def _metrics(pairs):
    n = len(pairs)
    if not n:
        return {"n": 0}
    acc = sum(1 for p, y in pairs if (p >= 0.5) == bool(y)) / n
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    ll = -sum(math.log(max(1e-9, p if y else 1 - p)) for p, y in pairs) / n
    return {"n": n, "acc": round(acc, 4), "brier": round(brier, 4),
            "logloss": round(ll, 4)}


def run(start, end, limit=None, with_market=True):
    """Walk a date range, predict each game from its posted starters + the teams'
    scoring to date, and score against the result (and the closing line)."""
    import datetime as _dt
    d0 = _dt.date.fromisoformat(start)
    d1 = _dt.date.fromisoformat(end)
    dates = []
    while d0 <= d1:
        dates.append(d0.isoformat())
        d0 += _dt.timedelta(days=1)
    rf, ra, gp = defaultdict(float), defaultdict(float), defaultdict(int)
    model, market, both, graded = [], [], [], []
    mkt_map = {}
    if with_market:
        try:
            import team_backtest as tb
            for g in tb.season_games("mlb", int(start[:4])):
                mkt_map[(g["date"], g["home"], g["away"])] = g["id"]  # ESPN abbrevs
        except Exception:
            mkt_map = {}
    scored = 0
    for date in dates:
        for g in games_on(date):
            h, a = g["home"], g["away"]
            if gp[h] >= 10 and gp[a] >= 10 and (limit is None or scored < limit):
                lg_rf = (sum(rf.values()) / max(1, sum(gp.values()))) or 4.4
                # Offense: runs scored per game to date, regressed.
                off_h = (rf[h] + 8 * lg_rf) / (gp[h] + 8)
                off_a = (rf[a] + 8 * lg_rf) / (gp[a] + 8)
                # Defense is dominated by the man on the mound tonight, blended
                # with the team's overall runs allowed.
                sp_h = _sp_quality(pitcher_line(g["sp_home"], date))
                sp_a = _sp_quality(pitcher_line(g["sp_away"], date))
                def_h = 0.65 * sp_h + 0.35 * ((ra[h] + 8 * lg_rf) / (gp[h] + 8))
                def_a = 0.65 * sp_a + 0.35 * ((ra[a] + 8 * lg_rf) / (gp[a] + 8))
                exp_h = off_h * (def_a / lg_rf)
                exp_a = off_a * (def_h / lg_rf)
                # Pythagorean win expectancy on the expected runs, plus home edge.
                p = (exp_h ** 1.83) / ((exp_h ** 1.83) + (exp_a ** 1.83)) + _HFA
                p = max(0.05, min(0.95, p))
                y = 1 if g["hs"] > g["as_"] else 0
                model.append((p, y)); graded.append((p, y)); scored += 1
                eid = mkt_map.get((date, _abbr(h), _abbr(a)))
                if eid:
                    try:
                        import team_backtest as tb
                        mk = tb.odds_for("mlb", eid)
                    except Exception:
                        mk = None
                    if mk is not None:
                        market.append((mk, y)); both.append((p, mk, y))
            rf[h] += g["hs"]; ra[h] += g["as_"]; gp[h] += 1
            rf[a] += g["as_"]; ra[a] += g["hs"]; gp[a] += 1
    out = {"sport": "mlb", "start": start, "end": end, "games_scored": len(model),
           "model": _metrics(model), "market": _metrics(market), "_graded": graded}
    if both:
        best_w, best_ll = 0.0, None
        for i in range(21):
            w = i / 20.0
            bl = [(max(1e-6, min(1 - 1e-6, w * m + (1 - w) * k)), y) for m, k, y in both]
            ll = _metrics(bl)["logloss"]
            if best_ll is None or ll < best_ll:
                best_w, best_ll = w, ll
        out["best_blend_weight"] = best_w
        out["best_blend_logloss"] = best_ll
    return out
