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
            "seeds": 7, "best_of": 1, "season": 2026},
    "nba": {"reg": 0.35, "home": 2.8, "k": 11.0, "ros_pts": 4.0, "key_pen": 0.0,
            "seeds": 8, "best_of": 7, "season": 2026},
    "nhl": {"reg": 0.30, "home": 0.30, "k": 0.95, "ros_pts": 0.55, "key_pen": 0.5,
            "seeds": 8, "best_of": 7, "season": 2026},
}


def ratings(league):
    """{team_id: {name, rating, base, mod, conf, division, avail}} for every team."""
    p = PARAMS[league]
    tm = {t["id"]: t for t in pro_data.teams(league)}
    grp = pro_data.groups(league)
    st = pro_data.standings(league, p["season"] - 1)
    out = {}
    for tid, t in tm.items():
        s = st.get(tid, {"diff_pg": 0.0, "wins": 0, "losses": 0})
        base = s["diff_pg"] * (1 - p["reg"])           # regress last year's margin
        av = pro_data.roster_avail(league, tid)
        mod = -av["out_index"] * p["ros_pts"]
        if av["key_out"]:
            mod -= p["key_pen"]
        g = grp.get(tid, {"conf": "?", "division": "?"})
        out[tid] = {"id": tid, "name": t["name"], "abbrev": t.get("abbrev"),
                    "rating": round(base + mod, 2), "base": round(base, 2),
                    "mod": round(mod, 2), "conf": g["conf"], "division": g["division"],
                    "prior": f"{s['wins']}-{s['losses']}", "avail": av}
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


def _bracket(seeds, rate, k, best_of, rng):
    """Run a single conference bracket from an ordered seed list; return winner."""
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
            pg = _wp(rate[hi]["rating"], rate[lo]["rating"], k, rate[hi]["home"])
            win = hi if rng.random() < _series_wp(pg, best_of) else lo
            nxt.append(win)
        if field:
            nxt.append(field.pop())
        field = nxt
        field.sort(key=lambda t: rate[t]["seed_rank"])
    return field[0] if field else (bye[0] if bye else None)


def _one_season(rate, schedule, conf_ids, p, rng):
    k, home = p["k"], p["home"]
    wins = defaultdict(int)
    for h, a in schedule:
        if rng.random() < _wp(rate[h]["rating"], rate[a]["rating"], k, home):
            wins[h] += 1
        else:
            wins[a] += 1
    champ = None
    conf_champs = []
    div_winners = set()
    made = set()
    finalists = []
    for conf, ids in conf_ids.items():
        seeds = _seed_conference(ids, wins, rate)[:p["seeds"]]
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
        w = _bracket(seeds, rate, k, p["best_of"], rng)
        conf_champs.append(w)
        finalists.append(w)
    # championship game / series between conference champs
    if len(finalists) >= 2:
        a, b = finalists[0], finalists[1]
        pg = _wp(rate[a]["rating"], rate[b]["rating"], k, 0.0)   # neutral site
        champ = a if rng.random() < _series_wp(pg, p["best_of"]) else b
    elif finalists:
        champ = finalists[0]
    return wins, champ, conf_champs, div_winners, made


def project(league, n=4000, seed=None):
    """Monte-Carlo the upcoming season; return the futures board."""
    p = PARAMS[league]
    rate = ratings(league)
    if not rate:
        return None
    schedule = pro_data.schedule(league, p["season"])
    if not schedule:
        return None
    conf_ids = defaultdict(list)
    for tid, r in rate.items():
        conf_ids[r["conf"]].append(tid)
    rng = random.Random(seed)

    champ = defaultdict(int); conf_w = defaultdict(int)
    div_w = defaultdict(int); playoff = defaultdict(int)
    win_sum = defaultdict(float)
    win_hist = defaultdict(lambda: defaultdict(int))     # tid -> wins -> count
    for _ in range(n):
        wins, c, ccs, dws, made = _one_season(rate, schedule, conf_ids, p, rng)
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

    teams = []
    for tid, r in rate.items():
        wh = win_hist[tid]
        teams.append({
            "id": tid, "name": r["name"], "abbrev": r["abbrev"],
            "conf": r["conf"], "division": r["division"], "prior": r["prior"],
            "rating": r["rating"], "base": r["base"], "roster_mod": r["mod"],
            "champ_pct": round(100 * champ[tid] / n, 1),
            "conf_pct": round(100 * conf_w[tid] / n, 1),
            "division_pct": round(100 * div_w[tid] / n, 1),
            "playoff_pct": round(100 * playoff[tid] / n, 1),
            "proj_wins": round(win_sum[tid] / n, 1),
            "win_dist": {str(w): c for w, c in sorted(wh.items())},
            "out_list": r["avail"]["out_list"],
            "key_out": r["avail"]["key_out"],
            "rookies": r["avail"]["rookies"],
        })
    teams.sort(key=lambda t: t["champ_pct"], reverse=True)
    return {"league": league, "n_sims": n, "season": p["season"],
            "games": len(schedule), "teams": teams}


def win_total_prob(team, line):
    """P(team wins >= `line` games) from its simulated win distribution."""
    dist = team.get("win_dist") or {}
    tot = sum(dist.values()) or 1
    hit = sum(c for w, c in dist.items() if int(w) >= line)
    return hit / tot
