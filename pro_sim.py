"""Roster-aware preseason projection for NFL / NBA / NHL.

This far out (offseason) every team is 0-0, so the model is a projection, not an
in-season Monte Carlo. Each team's strength is built from:

  * a backbone from last season's point/goal differential per game, regressed to
    the mean (sports are noisy year-to-year), then
  * a roster-availability adjustment from the live ESPN roster: players in the
    suspended or injured-reserve buckets dock the team, weighted by position, and
    the marquee position being out (NFL QB / NHL goalie) docks it harder.

We then Monte-Carlo the published upcoming schedule, seed each conference's
playoff field (division winners first, then by record), run the bracket
(single-game in the NFL, best-of-7 series in the NBA/NHL), and tally
championship / conference / division / make-playoffs odds plus a win-total
distribution — everything Kalshi prices as a season future.
"""

import math
import random
from collections import defaultdict

import pro_data

# Per-league sim knobs:
#   reg       - shrink last season's margin toward the mean (year-to-year noise),
#   home      - home-field edge in margin units,
#   k         - logistic scale: margin/k -> win probability,
#   ros_pts   - margin docked per unit of roster "out index",
#   key_pen   - extra margin docked when the marquee position is out,
#   seeds     - playoff teams per conference,
#   best_of   - playoff series length (1 = single game, NFL).
PARAMS = {
    "nfl": {"reg": 0.58, "home": 2.0, "k": 10.5, "ros_pts": 5.0, "key_pen": 2.5,
            "seeds": 7, "best_of": 1, "season": 2026, "family": "football"},
    "nba": {"reg": 0.35, "home": 2.8, "k": 11.0, "ros_pts": 4.0, "key_pen": 0.0,
            "seeds": 8, "best_of": 7, "season": 2026, "family": "basketball",
            "path": "nba"},
    "nhl": {"reg": 0.30, "home": 0.30, "k": 0.95, "ros_pts": 0.55, "key_pen": 0.5,
            "seeds": 8, "best_of": 7, "season": 2026, "family": "hockey",
            "ot_points": True},
}


# Franchise Super Bowl titles through SB LIX (Feb 2025) — DISPLAY ONLY. A ring
# from 1975 predicts nothing about next season (that roster is long gone), so
# titles never enter the rating; they're context on the board because a futures
# bettor wants them in view. Keyed by ESPN abbreviation.
SB_TITLES = {"PIT": 6, "NE": 6, "SF": 5, "DAL": 5, "GB": 4, "NYG": 4, "KC": 4,
             "DEN": 3, "LV": 3, "WSH": 3, "IND": 2, "MIA": 2, "BAL": 2, "TB": 2,
             "LAR": 2, "PHI": 2, "CHI": 1, "NYJ": 1, "NO": 1, "SEA": 1}
SB_TITLES_ASOF = "through SB LIX"


def ratings(league):
    """{team_id: {name, rating, base, mod, conf, division, avail}} for every team."""
    p = PARAMS[league]
    tm = {t["id"]: t for t in pro_data.teams(league)}
    grp = pro_data.groups(league)
    st = pro_data.standings(league, p["season"] - 1)
    # NFL: the quarterback layer. Last season's differential embeds last season's
    # QB; when the starter changed (trade / FA / retirement / rookie) the rating
    # swings by the QB delta, and a proven elite keeps signal the regression
    # would otherwise wash out.
    qb_map = {}
    if league == "nfl":
        try:
            qb_map = pro_data.nfl_qb_map(p["season"] - 1)
        except Exception:
            qb_map = {}
    out = {}
    for tid, t in tm.items():
        s = st.get(tid, {"diff_pg": 0.0, "wins": 0, "losses": 0})
        base = s["diff_pg"] * (1 - p["reg"])           # regress last year's margin
        av = pro_data.roster_avail(league, tid)
        mod = -av["out_index"] * p["ros_pts"]
        if av["key_out"]:
            mod -= p["key_pen"]
        qb = qb_map.get(tid)
        if qb:
            mod += qb["adj"]
        g = grp.get(tid, {"conf": "?", "division": "?"})
        out[tid] = {"id": tid, "name": t["name"], "abbrev": t.get("abbrev"),
                    "rating": round(base + mod, 2), "base": round(base, 2),
                    "mod": round(mod, 2), "conf": g["conf"], "division": g["division"],
                    "prior": f"{s['wins']}-{s['losses']}", "avail": av, "qb": qb}
    return out


def _wp(rh, ra, k, home):
    return 1.0 / (1.0 + math.exp(-((rh - ra) + home) / k))


def _series_wp(pg, best_of):
    """P(win a best-of-N series) given per-game win prob pg (higher seed has the
    home edge folded into pg). Closed-form over the clinching game."""
    if best_of <= 1:
        return pg
    need = best_of // 2 + 1
    # P(team reaches `need` wins before opponent) via negative binomial.
    p = 0.0
    for losses in range(need):                          # opp wins before we clinch
        p += math.comb(need - 1 + losses, losses) * (pg ** need) * ((1 - pg) ** losses)
    return p


def _seed_conference(team_ids, wins, rate):
    """Playoff seeds for one conference: division winners first (best record in
    each division), then the rest by record. Returns ordered list of team ids."""
    by_div = defaultdict(list)
    for tid in team_ids:
        by_div[rate[tid]["division"]].append(tid)
    div_winners = []
    for div, members in by_div.items():
        div_winners.append(max(members, key=lambda t: wins[t] + random.random() * 1e-6))
    div_winners.sort(key=lambda t: wins[t], reverse=True)
    rest = sorted((t for t in team_ids if t not in set(div_winners)),
                  key=lambda t: wins[t], reverse=True)
    return div_winners + rest


# ---- Play-by-play resolver -------------------------------------------------
# The season is played out one GAME at a time with each sport's real engine
# (possession-level basketball, shot-event hockey), not a normal-margin draw:
# every simulated game is resolved by scoring it, so upsets, blowouts and (in
# hockey) overtime points emerge from the same dynamics the slate cards use.

def _engine(league, rate):
    """Return a resolver `game(h_id, a_id, rng, hca)` for the league's engine, or
    None to fall back to the closed-form win probability. hca: +1 home hosts,
    0 neutral, -1 away hosts. Returns (winner_id, ot_loser_bool)."""
    p = PARAMS[league]
    fam = p.get("family")
    tmap = {t["id"]: t for t in pro_data.teams(league)}
    ab = {tid: (tmap.get(tid, {}).get("abbrev") or "") for tid in rate}
    try:
        if fam == "basketball":
            import basket
            R = basket.ratings(p["path"])
            cfg = basket.LEAGUES[p["path"]]
            lgp = (next(iter(R.values()))["lg_ppp"] if R else cfg["lg_ppp"])
            hcae = cfg["hca_eff"]
            eng = {tid: R.get(ab[tid]) for tid in rate}
            if sum(1 for v in eng.values() if v) < len(rate) * 0.6:
                return None

            def game(h, a, rng, hca):
                rh, ra = eng.get(h), eng.get(a)
                if not rh or not ra:
                    return (h if rng.random() < 0.5 else a), False
                adj = hcae if hca > 0 else (1.0 / hcae if hca < 0 else 1.0)
                eff_h = rh["off_ppp"] * (ra["def_ppp"] / lgp) * adj
                eff_a = ra["off_ppp"] * (rh["def_ppp"] / lgp) / adj
                pace = (rh["pace"] + ra["pace"]) / 2.0
                hh, aa = basket.quick_game(eff_h, eff_a, pace, rng)
                return (h if hh > aa else a), False
            return game
        if fam == "hockey":
            import hockey
            R = hockey.ratings()
            lg = hockey._LG_GOALS
            hcae = hockey._HCA_G
            eng = {tid: R.get(ab[tid]) for tid in rate}
            if sum(1 for v in eng.values() if v) < len(rate) * 0.6:
                return None

            def game(h, a, rng, hca):
                rh, ra = eng.get(h), eng.get(a)
                if not rh or not ra:
                    return (h if rng.random() < 0.5 else a), False
                adj = hcae if hca > 0 else (1.0 / hcae if hca < 0 else 1.0)
                lgg = rh.get("lg_g") or lg
                exp_h = rh["gf"] * (ra["ga"] / lgg) * adj
                exp_a = ra["gf"] * (rh["ga"] / lgg) / adj
                hh, aa, ot = hockey.quick_game(exp_h, exp_a, rng)
                return (h if hh > aa else a), bool(ot)
            return game
    except Exception:
        return None
    return None


def _play_series(hi, lo, best_of, resolver, rng):
    """Best-of-N series; higher seed `hi` hosts games 1,2,5,7 (2-2-1-1-1).
    Returns the winning team id."""
    if best_of <= 1:
        w, _ = resolver(hi, lo, rng, 1)
        return w
    need = best_of // 2 + 1
    wins = {hi: 0, lo: 0}
    hosts = (1, 1, -1, -1, 1, -1, 1)          # +1 => hi hosts, -1 => lo hosts
    g = 0
    while wins[hi] < need and wins[lo] < need:
        hca = hosts[g] if g < len(hosts) else 1
        h, a = (hi, lo) if hca > 0 else (lo, hi)
        w, _ = resolver(h, a, rng, 1)         # host always has home ice/court
        wins[w] += 1
        g += 1
    return hi if wins[hi] > wins[lo] else lo


def _bracket(seeds, rate, k, best_of, rng, resolver=None):
    """Run a single conference bracket from an ordered seed list; return winner.
    When a play-by-play resolver is given, each round is a real series; otherwise
    it collapses to the closed-form series probability."""
    field = list(seeds)
    # Bye for the #1 seed when the field is odd-shaped (NFL 7-seed format).
    bye = [field.pop(0)] if len(field) == 7 else []
    while len(field) + len(bye) > 1:
        nxt = list(bye)
        bye = []
        # pair best vs worst
        field.sort(key=lambda t: rate[t]["seed_rank"])
        while len(field) >= 2:
            hi = field.pop(0)
            lo = field.pop(-1)
            if resolver is not None:
                win = _play_series(hi, lo, best_of, resolver, rng)
            else:
                pg = _wp(rate[hi]["rating"], rate[lo]["rating"], k, rate[hi]["home"])
                win = hi if rng.random() < _series_wp(pg, best_of) else lo
            nxt.append(win)
        if field:
            nxt.append(field.pop())
        field = nxt
        field.sort(key=lambda t: rate[t]["seed_rank"])
    return field[0] if field else (bye[0] if bye else None)


def _one_season(rate, schedule, conf_ids, p, rng, game_p=None, resolver=None,
                base_wins=None, base_pts=None):
    k, home = p["k"], p["home"]
    ot_points = p.get("ot_points")
    wins = defaultdict(int, base_wins or {})
    # Points = seeding metric. Hockey: 2 per win + 1 per OT/SO loss; elsewhere it
    # tracks wins so the same seeding code serves every league.
    pts = defaultdict(int, base_pts or {})
    rnd = rng.random
    for i, (h, a) in enumerate(schedule):
        if resolver is not None:
            w, ot = resolver(h, a, rng, 1)
            l = a if w == h else h
        else:
            w = h if rnd() < game_p[i] else a
            l, ot = (a if w == h else h), False
        wins[w] += 1
        if ot_points:
            pts[w] += 2
            if ot:
                pts[l] += 1
        else:
            pts[w] += 1
    seed_metric = pts if ot_points else wins
    champ = None
    conf_champs = []
    div_winners = set()
    made = set()
    finalists = []
    for conf, ids in conf_ids.items():
        seeds = _seed_conference(ids, seed_metric, rate)[:p["seeds"]]
        made.update(seeds)
        # division winner = top seed of each division within the conference field
        seen_div = set()
        for tid in seeds:
            d = rate[tid]["division"]
            if d not in seen_div:
                seen_div.add(d)
                div_winners.add(tid)
        for rank, tid in enumerate(seeds):
            rate[tid]["seed_rank"] = rank
            rate[tid]["home"] = home               # higher seed hosts (folded per matchup)
        w = _bracket(seeds, rate, k, p["best_of"], rng, resolver)
        conf_champs.append(w)
        finalists.append(w)
    # championship game / series between conference champs
    if len(finalists) >= 2:
        a, b = finalists[0], finalists[1]
        if resolver is not None:
            champ = _play_series(a, b, p["best_of"], resolver, rng)   # 1-seed of each hosts
        else:
            pg = _wp(rate[a]["rating"], rate[b]["rating"], k, 0.0)    # neutral site
            champ = a if rng.random() < _series_wp(pg, p["best_of"]) else b
    elif finalists:
        champ = finalists[0]
    return wins, champ, conf_champs, div_winners, made


# Season windows as (start month, day) -> (end month, day) in ET: from a few
# weeks before the opener (so preseason futures are live while there is something
# to price) until the title is decided.
#
# Without this, project() ran the full 4,000-season Monte Carlo for every league
# year round -- in July it was still fetching NBA and NHL schedules and simulating
# a season that does not start for months. Wasted every night, and on a small host
# it is memory and CPU taken from the sports that ARE playing.
SEASON_WINDOW = {
    "nfl": ((8, 1), (2, 15)),       # camp through the Super Bowl
    "nba": ((9, 20), (6, 30)),      # media day through the Finals
    "nhl": ((9, 15), (6, 30)),      # camp through the Cup
}


def in_season(league, today=None):
    """Is this league close enough to playing to be worth simulating?

    Windows WRAP the new year -- an NBA season starting in September ends the
    following June -- so a plain start <= today <= end test is wrong for exactly
    the months that matter."""
    win = SEASON_WINDOW.get(league)
    if not win:
        return True                 # unknown league: never silently go dark
    if today is None:
        import clock
        today = clock.today_et()
    (sm, sd), (em, ed) = win
    cur, start, end = (today.month, today.day), (sm, sd), (em, ed)
    if start <= end:                # window sits inside one calendar year
        return start <= cur <= end
    return cur >= start or cur <= end        # window wraps into the next year


def project(league, n=4000, seed=None, workers=None):
    """Monte-Carlo the season one game at a time with the sport's engine; return
    the futures board. Completed games are locked to their real result and only
    the remainder is played, so the same call serves preseason (full schedule)
    and mid-season (banked wins + remaining games).

    Returns None out of season, BEFORE any network fetch or simulation."""
    if not in_season(league):
        return None
    # Fan the seasons out across cores (each worker runs its slice single-process).
    import mp_season
    par = mp_season.run("pro_sim", "project", {"league": league}, n, seed,
                        team_key="id",
                        avg_fields=["champ_pct", "conf_pct", "division_pct",
                                    "playoff_pct", "proj_wins"],
                        sum_fields=["win_dist"], workers=workers)
    if par is not None:
        par["teams"].sort(key=lambda t: t["champ_pct"], reverse=True)
        return par
    p = PARAMS[league]
    rate = ratings(league)
    if not rate:
        return None
    # In-season snapshot (locked finals + remaining games); preseason this is the
    # full schedule with empty base tallies.
    state = {}
    try:
        state = pro_data.season_state(league, p["season"])
    except Exception:
        state = {}
    schedule = state.get("remaining")
    if not schedule:
        schedule = pro_data.schedule(league, p["season"])
    if not schedule:
        return None
    schedule = [(h, a) for (h, a) in schedule if h in rate and a in rate]
    base_wins = {tid: w for tid, w in (state.get("base_wins") or {}).items() if tid in rate}
    base_otl = state.get("base_otl") or {}
    ot_points = p.get("ot_points")
    base_pts = ({tid: base_wins.get(tid, 0) * 2 + base_otl.get(tid, 0)
                 for tid in rate} if ot_points else None)

    # Flat-playoff leagues seed league-wide, not by conference.
    conf_ids = defaultdict(list)
    if p.get("flat_playoff"):
        conf_ids["League"] = list(rate)
    else:
        for tid, r in rate.items():
            conf_ids[r["conf"]].append(tid)

    resolver = _engine(league, rate)
    rng = random.Random(seed)
    k, home = p["k"], p["home"]
    # Closed-form fallback probs (only used when the engine is unavailable).
    game_p = (None if resolver is not None
              else [_wp(rate[h]["rating"], rate[a]["rating"], k, home) for h, a in schedule])

    champ = defaultdict(int); conf_w = defaultdict(int)
    div_w = defaultdict(int); playoff = defaultdict(int)
    win_sum = defaultdict(float)
    win_hist = defaultdict(lambda: defaultdict(int))     # tid -> wins -> count
    for _ in range(n):
        wins, c, ccs, dws, made = _one_season(
            rate, schedule, conf_ids, p, rng, game_p, resolver, base_wins, base_pts)
        if c is not None:
            champ[c] += 1
        for cc in ccs:
            if cc is not None:
                conf_w[cc] += 1
        for d in dws:
            div_w[d] += 1
        for m in made:
            playoff[m] += 1
        for tid, w in wins.items():
            win_sum[tid] += w
            win_hist[tid][w] += 1

    flat = bool(p.get("flat_playoff"))
    teams = []
    for tid, r in rate.items():
        wh = win_hist[tid]
        teams.append({
            "id": tid, "name": r["name"], "abbrev": r["abbrev"],
            "conf": r["conf"], "division": r["division"], "prior": r["prior"],
            "rating": r["rating"], "base": r["base"], "roster_mod": r["mod"],
            "champ_pct": round(100 * champ[tid] / n, 1),
            "conf_pct": None if flat else round(100 * conf_w[tid] / n, 1),
            "division_pct": None if flat else round(100 * div_w[tid] / n, 1),
            "playoff_pct": round(100 * playoff[tid] / n, 1),
            "proj_wins": round(win_sum[tid] / n, 1),
            "win_dist": {str(w): c for w, c in sorted(wh.items())},
            "out_list": r["avail"]["out_list"],
            "key_out": r["avail"]["key_out"],
            "rookies": r["avail"]["rookies"],
            "qb": r.get("qb"),
            "titles": SB_TITLES.get(r.get("abbrev") or "", 0) if league == "nfl" else None,
        })
    teams.sort(key=lambda t: t["champ_pct"], reverse=True)
    return {"league": league, "n_sims": n, "season": p["season"],
            "engine": "pbp" if resolver is not None else "rating",
            "games": len(schedule), "games_played": state.get("played", 0),
            "teams": teams,
            "titles_asof": SB_TITLES_ASOF if league == "nfl" else None}


def win_total_prob(team, line):
    """P(team wins >= `line` games) from its simulated win distribution."""
    dist = team.get("win_dist") or {}
    tot = sum(dist.values()) or 1
    hit = sum(c for w, c in dist.items() if int(w) >= line)
    return hit / tot
