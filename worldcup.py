"""World Cup simulator: model the rest of the tournament from where it stands.

Pulls the live group tables + fixtures from ESPN, rates every team with a curated
national-team Elo prior that is then updated through the games already played (so
the rating reflects tournament form, not just reputation), and Monte-Carlos the
remaining group games and the knockout bracket with a Poisson goal model.

Out of one run we get, per team, the odds to advance / win the group / reach each
knockout round / win the Cup, and per upcoming match the 3-way result, total-goals
and both-teams-to-score probabilities — exactly the markets Kalshi and Polymarket
price, and the model legs the World Cup combo maker needs.
"""

import math
import random
from collections import defaultdict

import racing                       # disk-cache + JSON GET helpers

SITE = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world"
WEB = "https://site.web.api.espn.com/apis/v2/sports/soccer/fifa.world"

# Curated national-team strength priors (~Elo scale). Approximate FIFA-tier
# ratings for the field; anyone unlisted defaults to 1700. The in-tournament Elo
# update then moves these by actual results, so exact priors matter less as the
# tournament progresses.
_RATING = {
    "Argentina": 2090, "France": 2070, "Spain": 2060, "England": 2030,
    "Brazil": 2030, "Portugal": 2000, "Netherlands": 1990, "Belgium": 1960,
    "Germany": 1965, "Croatia": 1930, "Uruguay": 1925, "Colombia": 1915,
    "Morocco": 1910, "Italy": 1950, "Switzerland": 1880, "United States": 1870,
    "Mexico": 1860, "Senegal": 1865, "Japan": 1860, "Ecuador": 1840,
    "Austria": 1835, "Türkiye": 1830, "Ukraine": 1825, "Denmark": 1900,
    "Sweden": 1815, "Norway": 1840, "Iran": 1810, "Australia": 1805,
    "South Korea": 1800, "Ivory Coast": 1800, "Egypt": 1795, "Nigeria": 1840,
    "Scotland": 1790, "Panama": 1740, "Paraguay": 1790, "Qatar": 1760,
    "Canada": 1800, "Ghana": 1785, "Tunisia": 1760, "Algeria": 1790,
    "Saudi Arabia": 1740, "Uzbekistan": 1730, "Jordan": 1700, "Iraq": 1710,
    "South Africa": 1740, "Cape Verde": 1710, "Bosnia-Herzegovina": 1760,
    "New Zealand": 1690, "Czechia": 1800, "Curaçao": 1640, "Haiti": 1640,
    "Congo DR": 1720,
}
_DEFAULT_RATING = 1700
_MU_TOTAL = 2.65          # avg goals per WC match (both teams)
_ELO_PER_GOAL = 170.0     # Elo points ~ one goal of supremacy
_KO_ROUNDS = [("r32", 32), ("r16", 16), ("qf", 8), ("sf", 4), ("final", 2)]


def _get(url):
    return racing._get_json(url)


def _state():
    """{groups, table, played, remaining, fixtures, team_group} from ESPN, cached
    ~20 min since the tournament is live."""
    def build():
        s = _get(f"{WEB}/standings")
        table, team_group, groups = {}, {}, defaultdict(list)
        for g in s.get("children", []):
            gname = g.get("name", "")
            for e in g.get("standings", {}).get("entries", []):
                nm = e["team"]["displayName"]
                st = {x["name"]: x.get("value") for x in e.get("stats", [])}
                table[nm] = {"pts": st.get("points") or 0, "gp": st.get("gamesPlayed") or 0,
                             "gf": st.get("pointsFor") or 0, "ga": st.get("pointsAgainst") or 0,
                             "gd": st.get("pointDifferential") or 0, "group": gname}
                team_group[nm] = gname
                groups[gname].append(nm)
        # All fixtures across the tournament window.
        played, remaining, fixtures = [], [], []
        sb = _get(f"{SITE}/scoreboard?dates=20260611-20260719")
        for ev in sb.get("events", []):
            comp = (ev.get("competitions") or [{}])[0]
            cs = comp.get("competitors", [])
            if len(cs) != 2:
                continue
            home = next((c for c in cs if c.get("homeAway") == "home"), cs[0])
            away = next((c for c in cs if c.get("homeAway") == "away"), cs[1])
            hn = home["team"]["displayName"]
            an = away["team"]["displayName"]
            state = ((ev.get("status") or {}).get("type") or {}).get("state")
            date = (ev.get("date") or "")[:10]
            # Knockout slots can carry placeholder names until groups resolve.
            known = hn in _RATING or hn in table
            known_a = an in _RATING or an in table
            if state == "post":
                try:
                    hg, ag = int(home.get("score")), int(away.get("score"))
                except (TypeError, ValueError):
                    continue
                played.append({"home": hn, "away": an, "hg": hg, "ag": ag, "date": date})
            elif known and known_a:
                stage = "group" if (hn in team_group and team_group.get(hn) ==
                                    team_group.get(an)) else "ko"
                fx = {"home": hn, "away": an, "date": date, "stage": stage}
                remaining.append(fx)
                fixtures.append(fx)
        return {"groups": dict(groups), "table": table, "team_group": team_group,
                "played": played, "remaining": remaining}
    return racing._cached(("wc_state",), 1200, build) or {}


def ratings(state=None):
    """Elo prior updated through every played match -> current tournament strength."""
    state = state or _state()
    R = {t: float(_RATING.get(t, _DEFAULT_RATING)) for t in state.get("table", {})}
    for t in state.get("team_group", {}):
        R.setdefault(t, float(_RATING.get(t, _DEFAULT_RATING)))
    for m in state.get("played", []):
        a, b = m["home"], m["away"]
        if a not in R or b not in R:
            continue
        ra, rb = R[a], R[b]
        ea = 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))
        res = 0.5 if m["hg"] == m["ag"] else (1.0 if m["hg"] > m["ag"] else 0.0)
        gd = abs(m["hg"] - m["ag"])
        g = 1.0 if gd <= 1 else (1.5 if gd == 2 else (11 + gd) / 8.0)   # FIFA MoV
        delta = 40.0 * g * (res - ea)
        R[a] += delta
        R[b] -= delta
    return R


def _lambdas(ra, rb, host_home=False):
    """Expected goals for each side from the rating gap (a small host edge)."""
    sup = (ra - rb + (25 if host_home else 0)) / _ELO_PER_GOAL
    la = max(0.18, _MU_TOTAL / 2 + sup / 2)
    lb = max(0.18, _MU_TOTAL / 2 - sup / 2)
    return la, lb


def _poisson(lam, rng):
    """Knuth sampler — fine for the small means here."""
    L = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


def _play(a, b, R, rng, knockout=False):
    """Return (goals_a, goals_b, winner). Knockout never ends level: a draw goes to
    extra time (a little more scoring) and then penalties (near coin-flip, nudged
    by rating)."""
    la, lb = _lambdas(R[a], R[b])
    ga, gb = _poisson(la, rng), _poisson(lb, rng)
    if not knockout:
        return ga, gb, (a if ga > gb else b if gb > ga else None)
    if ga == gb:
        ga += _poisson(la * 0.33, rng)
        gb += _poisson(lb * 0.33, rng)
    if ga == gb:                                   # penalties
        pa = 0.5 + max(-0.18, min(0.18, (R[a] - R[b]) / 4000.0))
        return ga, gb, (a if rng.random() < pa else b)
    return ga, gb, (a if ga > gb else b)


def _group_order(teams, base, sim_results, rng):
    """Final 1-4 order of a group given current table + this sim's remaining
    results, tie-broken by points, then goal difference, then goals for."""
    pts = {t: base[t]["pts"] for t in teams}
    gd = {t: base[t]["gd"] for t in teams}
    gf = {t: base[t]["gf"] for t in teams}
    for (a, b, ga, gb) in sim_results:
        if ga > gb:
            pts[a] += 3
        elif gb > ga:
            pts[b] += 3
        else:
            pts[a] += 1; pts[b] += 1
        gd[a] += ga - gb; gd[b] += gb - ga
        gf[a] += ga; gf[b] += gb
    return sorted(teams, key=lambda t: (pts[t], gd[t], gf[t], rng.random()), reverse=True)


def simulate(n=20000, seed=None):
    state = _state()
    if not state.get("table"):
        return None
    R0 = ratings(state)
    table = state["table"]
    groups = state["groups"]
    rng = random.Random(seed)

    teams = list(table)
    champ = defaultdict(int)
    advance = defaultdict(int); win_group = defaultdict(int)
    reach = {rd: defaultdict(int) for rd, _ in _KO_ROUNDS}
    # Per upcoming match outcome tallies (group + any scheduled KO with known teams).
    upcoming = [m for m in state["remaining"]]
    m_w = [defaultdict(int) for _ in upcoming]      # 'home'/'away'/'draw'
    m_tot = [defaultdict(int) for _ in upcoming]    # total goals
    m_btts = [0 for _ in upcoming]

    for _ in range(n):
        # 1) finish the groups
        sim_by_group = defaultdict(list)
        for mi, m in enumerate(upcoming):
            if m["stage"] != "group":
                continue
            a, b = m["home"], m["away"]
            ga, gb, _ = _play(a, b, R0, rng)
            sim_by_group[table[a]["group"]].append((a, b, ga, gb))
            _tally_match(mi, a, b, ga, gb, m_w, m_tot, m_btts)
        qualifiers = []
        thirds = []
        for gname, members in groups.items():
            order = _group_order(members, table, sim_by_group.get(gname, []), rng)
            win_group[order[0]] += 1
            for t in order[:2]:
                advance[t] += 1
            qualifiers.extend(order[:2])
            if len(order) >= 3:
                thirds.append(order[2])
        # 8 best third-placed teams advance (48-team format) — rank by rating proxy.
        thirds.sort(key=lambda t: R0[t] + rng.random() * 50, reverse=True)
        for t in thirds[:8]:
            advance[t] += 1
        bracket = qualifiers + thirds[:8]
        # 2) knockout: random balanced single-elimination among the 32 (no official
        # bracket template until groups resolve -> random draw = realistic path
        # variance).
        rng.shuffle(bracket)
        for rd, size in _KO_ROUNDS:
            for t in bracket:
                reach[rd][t] += 1
            if len(bracket) < 2:
                break
            nxt = []
            for i in range(0, len(bracket) - 1, 2):
                a, b = bracket[i], bracket[i + 1]
                _, _, w = _play(a, b, R0, rng, knockout=True)
                nxt.append(w)
            bracket = nxt
        if bracket:
            champ[bracket[0]] += 1

    # also record KO upcoming matches that have known teams (props)
    for mi, m in enumerate(upcoming):
        if m["stage"] == "group":
            continue
        a, b = m["home"], m["away"]
        for _ in range(min(n, 4000)):
            ga, gb, _ = _play(a, b, R0, rng, knockout=True)
            _tally_match(mi, a, b, ga, gb, m_w, m_tot, m_btts, denom=min(n, 4000))

    return _assemble(state, R0, teams, n, champ, advance, win_group, reach,
                     upcoming, m_w, m_tot, m_btts)


def _tally_match(mi, a, b, ga, gb, m_w, m_tot, m_btts, denom=None):
    if ga > gb:
        m_w[mi]["home"] += 1
    elif gb > ga:
        m_w[mi]["away"] += 1
    else:
        m_w[mi]["draw"] += 1
    m_tot[mi][ga + gb] += 1
    if ga >= 1 and gb >= 1:
        m_btts[mi] += 1


def _assemble(state, R, teams, n, champ, advance, win_group, reach,
              upcoming, m_w, m_tot, m_btts):
    table = state["table"]
    team_rows = []
    for t in teams:
        team_rows.append({
            "name": t, "group": table[t]["group"], "rating": round(R[t]),
            "pts": table[t]["pts"], "gp": table[t]["gp"], "gd": table[t]["gd"],
            "champion_pct": round(100 * champ[t] / n, 1),
            "finalist_pct": round(100 * reach["final"][t] / n, 1),
            "advance_pct": round(100 * advance[t] / n, 1),
            "win_group_pct": round(100 * win_group[t] / n, 1),
            "r16_pct": round(100 * reach["r16"][t] / n, 1),
            "qf_pct": round(100 * reach["qf"][t] / n, 1),
            "sf_pct": round(100 * reach["sf"][t] / n, 1),
        })
    team_rows.sort(key=lambda r: r["champion_pct"], reverse=True)

    matches = []
    for mi, m in enumerate(upcoming):
        w = m_w[mi]
        tot = sum(w.values()) or 1
        td = m_tot[mi]
        ttot = sum(td.values()) or 1
        def over(line):
            return round(100 * sum(c for g, c in td.items() if g > line) / ttot, 1)
        mean_goals = round(sum(g * c for g, c in td.items()) / ttot, 2)
        matches.append({
            "home": m["home"], "away": m["away"], "date": m["date"], "stage": m["stage"],
            "p_home": round(100 * w["home"] / tot, 1), "p_draw": round(100 * w["draw"] / tot, 1),
            "p_away": round(100 * w["away"] / tot, 1),
            "mean_goals": mean_goals, "over15": over(1), "over25": over(2), "over35": over(3),
            "btts_pct": round(100 * m_btts[mi] / tot, 1),
        })
    matches.sort(key=lambda r: r["date"])
    return {"sport": "worldcup", "n_sims": n, "teams": team_rows, "matches": matches,
            "groups_left": sum(1 for m in upcoming if m["stage"] == "group")}
