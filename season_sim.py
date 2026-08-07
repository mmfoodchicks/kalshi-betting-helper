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

import clock
import random
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from math import comb

import baseball
import kalshi

# Kalshi futures series. Winner-style series have one YES market per team
# (ticker ...-{ABBR}); we map each to one of our simulated probabilities.
# World Series winner. The live series is KXMLB -- "Will <team> win the 2026 Pro
# Baseball Championship", one YES market per team, 30 of them, all quoted.
#
# The two tickers here before it found NOTHING, so every World Series row on the
# board carried kalshi_cents=null and every "edge" was measured against
# Polymarket alone -- against a venue that is not where the bets get placed.
# KXMLBWS returns no open markets at all, and KXMLBWORLD is the World Baseball
# CLASSIC: a different tournament, played by national teams, in a different year.
# It would have been a wrong price rather than a missing one had it ever quoted.
#
# Kept in the tuple after KXMLB purely as fallbacks in case Kalshi renames the
# live one mid-season; the first series that returns markets wins.
_WS_SERIES = ("KXMLB", "KXMLBWS")
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
    start = clock.today_et().isoformat()
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


# How many starters a team actually uses once it reaches October. A best-of-five
# needs three and a best-of-seven four; nobody's fifth starter throws a playoff
# inning.
_PLAYOFF_ROT = 4
_SEASON_ROT = 5        # and how many turn over in a normal week
_MIN_GS = 5

# And how many RELIEVERS. A regular-season bullpen ERA is the whole pen, mop-up
# arms and all; an October pen is the three or four men who get the ball in a
# one-run game, plus whoever eats the rare blowout. Leaving it at the season
# figure made the same mistake on the back half of the game that the six-man
# rotation made on the front half.
_PLAYOFF_PEN = 5
_MIN_PEN_IP = 15.0
_PEN_IP_REGRESS = 25.0     # relievers throw ~65 IP, so a starter's constant is too heavy

# Batters a pitcher plunks, per 9. roster_lines carries no HBP column, and FIP
# needs one. Charging every arm the league rate is the honest prior — it ranks
# nobody up or down, and it keeps FIP on the scale FIP_CONSTANT was fitted for
# (dropping the term outright would shift every FIP down ~0.15 against a league
# ERA that still includes them, which does NOT cancel in a ratio).
_LG_HBP9 = 0.45


def _po_weights(k, games):
    """Share of `games` starts each of `k` rotation arms gets, ace first.

    A best-of-seven is not four equal turns: the rotation runs 1-2-3-4-1-2-3, so
    the top three start twice and the fourth once. Averaging the best four FLAT
    (the first cut of this) gave the #4 the same say as the ace. A five-man
    regular-season rotation over five games is the same function, and comes out
    flat, which is the point — one rule, two series lengths.
    """
    if k <= 0:
        return []
    counts = [0] * k
    for g in range(games):
        counts[g % k] += 1
    return [c / games for c in counts]


def _rot_ra9(arms, k, games):
    """RA/9 of the top `k` arms (best first) over a `games`-long turn of the order.

    Both halves of the year read this, which makes one thing true BY PROOF rather
    than by hope. Over five ranked arms a1<=..<=a5, the season is flat fifths and
    October is (2,2,2,1)/7, so

        season - october = (-3a1 - 3a2 - 3a3 + 2a4 + 7a5) / 35 >= 0

    since 2a4 + 7a5 >= 9a3 >= 3a1 + 3a2 + 3a3. A club's October rotation can
    therefore never rate worse than its season rotation. Two earlier cuts of this
    both broke that — one weighted the season by games started (a record of who
    pitched in April, not a forecast of who takes the next turn), and the next
    still let a thin staff rate worse in October because BOTH halves drew on the
    same three arms and the weight profiles crossed. Hence the padding in
    _rotations: five arms go in, always.
    """
    top = arms[:max(1, k)]
    return sum(w * a["ra9"] for w, a in zip(_po_weights(len(top), games), top))


def _sp_ra9(p, lg):
    """One starter's RA/9 through baseball._starter_ra9 — the SAME three-way
    ERA + FIP + WHIP blend the daily board grades a probable pitcher with.

    An earlier version of this regressed raw ERA and nothing else, which meant
    the season board and the game board disagreed about how good a pitcher is:
    ERA alone rewards the defence behind him and the order the hits arrived in,
    and it is the least predictive of the three for what he does NEXT.

    roster_lines carries no HBP, and no last-5 line, so this is the season-shape
    read: FIP gets a league-rate HBP prior and the recent-form blend sits out.
    """
    ip = baseball._ip_float(p.get("ip"), 0.0)
    try:
        era = float(p.get("era"))
    except (TypeError, ValueError):
        return None
    if ip <= 0 or era <= 0:
        return None
    return baseball._starter_ra9({"season": {
        "era": era, "whip": baseball._f(p.get("whip"), lg.get("whip") or 1.30),
        "ip": ip, "gs": p.get("gs") or 0,
        "hr": baseball._f(p.get("hr")), "bb": baseball._f(p.get("bb")),
        "k": baseball._f(p.get("k")), "hbp": ip * _LG_HBP9 / 9.0,
    }}, lg)


def _pen_ra9(p, lg):
    """One reliever's RA/9, regressed by innings toward the league bullpen."""
    ip = baseball._ip_float(p.get("ip"), 0.0)
    try:
        era = float(p.get("era"))
    except (TypeError, ValueError):
        return None
    if ip < _MIN_PEN_IP or era <= 0:
        return None
    rel = ip / (ip + _PEN_IP_REGRESS)
    era_eff = rel * era + (1 - rel) * (lg.get("bp_era") or 4.0)
    whip = baseball._f(p.get("whip"))
    whip_eff = (rel * whip + (1 - rel) * (lg.get("bp_whip") or 1.28)) if whip > 0 \
        else (lg.get("bp_whip") or 1.28)
    return baseball._bullpen_ra9({"era": era_eff, "whip": whip_eff}, lg)


def _rotations(season, lg):
    """team_id -> {sp_season, sp_playoff, bp_season, bp_playoff} in RA/9.

    The season sim priced every game off ONE team ERA, so facing a rotation's
    ace and facing its fifth starter were the same event, and a team that is
    four deep in front-line arms looked identical to one with a flat staff. That
    is wrong all year and badly wrong in October, when the bottom of a rotation
    simply does not pitch, and neither does the back of a bullpen.

    Over 162 games the per-START effect is second order — every arm gets his ~32
    turns, so what moves a win total is the staff aggregate — but WHICH aggregate
    matters: this one is regressed by innings, blended off FIP and WHIP as well
    as ERA, and counts only the men actually starting now. In a seven-game series
    it is first order, and that is what sp_playoff / bp_playoff are for.
    """
    import deep_data
    bp_map = baseball._bullpen_map(season) or {}
    lg_era = lg.get("era") or 4.20
    out = {}
    for tid in list(baseball._pitching_map(season) or {}):
        try:
            rl = deep_data.roster_lines(tid, season) or {}
        except Exception:
            continue
        arms, pen = [], []
        for r in rl.values():
            if r.get("il"):
                continue
            p = r.get("pit") or {}
            # MLB writes innings base-3 after the point: "133.2" is 133 and TWO
            # THIRDS, not 133.2. baseball._ip_float already knows that; parsing it
            # as a plain float would understate every workload and so under-
            # regress every small sample toward league average.
            #
            # Every arm lands on exactly one side of this line. Requiring a
            # reliever to have made ZERO starts (the first cut of this) threw out
            # every swingman — several clubs came back with one or two qualifying
            # relievers, and "the best five of two" is not a short bullpen, it is
            # a small sample wearing one.
            if (p.get("gs") or 0) >= _MIN_GS:
                ra9 = _sp_ra9(p, lg)
                if ra9 is not None:
                    arms.append({"gs": p["gs"], "ra9": ra9})
            else:
                ra9 = _pen_ra9(p, lg)
                if ra9 is not None:
                    pen.append(ra9)
        if not arms:
            continue
        # A club whose active roster only shows three qualifying starters is not
        # running a three-man rotation — the other turns went to arms this filter
        # cannot see (on the IL, traded in, still short of _MIN_GS). Price those
        # turns at a league-average starter rather than handing them to whoever
        # happens to have survived the filter, and the ranked list is always five
        # deep, which is what makes _rot_ra9's ordering proof hold.
        n_real = len(arms)
        arms += [{"ra9": lg_era}] * max(0, _SEASON_ROT - n_real)
        arms.sort(key=lambda a: a["ra9"])
        sp_season = _rot_ra9(arms, _SEASON_ROT, _SEASON_ROT)
        sp_playoff = _rot_ra9(arms, _PLAYOFF_ROT, 7)
        # Regular season: the team's real relief line, mop-up innings included.
        bp_season = float((bp_map.get(tid) or {}).get("era") or lg_era)
        # October: the arms that actually get a lead to protect. A club short of
        # qualifying arms is topped up with its own season pen rather than having
        # the whole bullpen rated off two men — partial evidence, partial credit.
        pen_best = sorted(pen)[:_PLAYOFF_PEN]
        pen_best += [bp_season] * (_PLAYOFF_PEN - len(pen_best))
        bp_playoff = sum(pen_best) / len(pen_best)
        out[tid] = {"sp_season": sp_season, "sp_playoff": sp_playoff,
                    "bp_season": bp_season, "bp_playoff": min(bp_playoff, bp_season),
                    "n_arms": n_real, "n_pen": len(pen)}
    return out


def _pit_factor(rot, lg, playoff):
    """A team's run-prevention factor vs league, from its rotation + bullpen.

    Same decomposition the game model uses (baseball.py: game_ra9 =
    SP_INNINGS_WEIGHT * sp_ra9 + the rest from the pen), so the season sim and
    the daily board are reading one definition of pitching rather than two."""
    lg_era = lg.get("era") or 4.20
    if not rot:
        return 1.0
    w = baseball.SP_INNINGS_WEIGHT
    sp = rot["sp_playoff"] if playoff else rot["sp_season"]
    bp = rot["bp_playoff"] if playoff else rot["bp_season"]
    return (w * sp + (1 - w) * bp) / lg_era if lg_era else 1.0


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
    """P(team with single-game prob p wins a series, first to `need` wins).

    Venue-blind: every game is played at the same single-game probability. Kept
    for callers that genuinely have no home field to model."""
    total = 0.0
    for losses in range(need):           # opponent wins before we clinch
        total += comb(need - 1 + losses, losses) * (p ** need) * ((1 - p) ** losses)
    return total


# Which games of a series the HOST plays at home, by series length. MLB's formats:
# best-of-3 wild card is all three at the higher seed; best-of-5 is 2-2-1; and
# best-of-7 is 2-3-2. Indexed from game 1.
_HOME_GAMES = {2: (1, 2, 3), 3: (1, 2, 5), 4: (1, 2, 6, 7)}


def _series_p_hf(p_home, p_away, need):
    """P(the HOST wins the series), with the venue alternating by MLB's format.

    The postseason used to run entirely on `npr()` -- a neutral single-game
    probability with home_field=False -- so no playoff series had any home
    advantage at all. That is not a small omission in a best-of-seven: the host
    plays four of the seven at home, and the regular-season home edge is the one
    thing every one of those games shares.

    `p_home` is the host's win probability AT HOME, `p_away` his probability on
    the road. Exact rather than simulated: enumerate every sequence of wins and
    losses that ends the series, multiplying the per-game probability that the
    venue for that game implies."""
    homes = _HOME_GAMES.get(need)
    if not homes:
        return _series_p(p_home, need)
    total = 0.0
    max_games = 2 * need - 1

    def walk(game, w, l, prob):
        nonlocal total
        if w == need:
            total += prob
            return
        if l == need or game > max_games:
            return
        p = p_home if game in homes else p_away
        walk(game + 1, w + 1, l, prob * p)
        walk(game + 1, w, l + 1, prob * (1 - p))

    walk(1, 0, 0, 1.0)
    return total


def simulate(season=None, n=4000):
    season = season or str(clock.today_et().year)
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

    # Per-pitcher run prevention, for BOTH halves of the year. _strength() rates a
    # staff by its combined team ERA, which takes a rotation's sequencing luck and
    # its defence at face value and keeps counting arms who stopped starting in
    # May. The rotation build regresses each man by his own innings and blends
    # ERA with FIP and WHIP, so the rating tracks the pitchers rather than the
    # record. Best effort: on a roster-fetch failure the old team-ERA rating
    # stands rather than the board failing.
    try:
        rot = _rotations(season, lg)
    except Exception:
        rot = {}
    teams = {tid: ({"off": t["off"], "pit": _pit_factor(rot[tid], lg, False)}
                   if tid in rot else t)
             for tid, t in teams.items()}
    # October is played by a different pitching staff than August is. Teams keep
    # their offence and swap in the best-four rotation and the short bullpen the
    # postseason actually uses; a team deep in front-line arms gains, a flat
    # staff barely moves.
    teams_po = {tid: ({"off": t["off"], "pit": _pit_factor(rot[tid], lg, True)}
                      if tid in rot else t)
                for tid, t in teams.items()}

    # Pre-compute per-matchup home win prob (regular season has home field).
    pmap = {m: _win_prob(m[0], m[1], teams, lg) for m in set(games)}
    # Neutral single-game probs for the postseason (cache lazily).
    neut = {}

    def npr(a, b):
        if (a, b) not in neut:
            p = _win_prob(a, b, teams_po, lg, home_field=False)
            neut[(a, b)] = p
            neut[(b, a)] = 1 - p
        return neut[(a, b)]

    hf = {}

    def hpr(host, road):
        """(host's P(win) at home, host's P(win) on the road) for a playoff pair."""
        if (host, road) not in hf:
            at_home = _win_prob(host, road, teams_po, lg, home_field=True)
            on_road = 1.0 - _win_prob(road, host, teams_po, lg, home_field=True)
            hf[(host, road)] = (at_home, on_road)
        return hf[(host, road)]

    def series(host, road, need):
        """P(host wins), with home field where MLB actually grants it."""
        ph, pa = hpr(host, road)
        return _series_p_hf(ph, pa, need)

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
            w36 = seeds[2] if rnd() < series(seeds[2], seeds[5], _WC_NEED) else seeds[5]
            w45 = seeds[3] if rnd() < series(seeds[3], seeds[4], _WC_NEED) else seeds[4]
            # Division Series (best-of-5): 1 vs lower remaining seed, 2 vs other.
            ds_lo, ds_hi = (w36, w45) if seeds.index(w36) > seeds.index(w45) else (w45, w36)
            d1 = seeds[0] if rnd() < series(seeds[0], ds_lo, _DS_NEED) else ds_lo
            d2 = seeds[1] if rnd() < series(seeds[1], ds_hi, _DS_NEED) else ds_hi
            # LCS (best-of-7) -> pennant.
            lcs_host, lcs_road = (d1, d2) if seeds.index(d1) <= seeds.index(d2) else (d2, d1)
            champ = (lcs_host if rnd() < series(lcs_host, lcs_road, _LCS_NEED)
                     else lcs_road)
            champs[lg_id] = champ
            pennants[champ] += 1
        # World Series (best-of-7).
        lg_ids = list(champs.keys())
        if len(lg_ids) == 2:
            a, b = champs[lg_ids[0]], champs[lg_ids[1]]
            # Home field goes to the pennant winner with the better REGULAR-SEASON
            # record. It has nothing to do with the All-Star Game -- that rule ran
            # 2003-2016 and the 2017 CBA scrapped it. `wins` is this simulated
            # season's win total, so the host is decided inside each iteration
            # rather than assumed. Ties break on the coin the tiebreaker ladder
            # would eventually reach.
            if wins[a] > wins[b] or (wins[a] == wins[b] and rnd() < 0.5):
                host, road = a, b
            else:
                host, road = b, a
            winner = host if rnd() < series(host, road, _WS_NEED) else road
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
    season = season or str(clock.today_et().year)
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
            fl = m.get("floor")
            if fl is None:
                continue
            # Kalshi win-total floors are half-strikes (92.5 = "wins ABOVE 92.5"
            # = 93+ wins). int(92.5)=92 mislabeled the market AND made deep_board
            # (which re-derives counts from row["line"]) price the wrong bucket.
            line = int(fl) + 1 if fl != int(fl) else int(fl)
            model = 100.0 * sum(1 for w in s if w >= line) / len(s)
            row = _futrow(t, "win_total", f"{t['name']} {line}+ wins",
                          model, m, None)
            row["line"] = line
            ladder.append(row)
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


def futures_board(season=None, sim=None, n=4000):
    """The futures redesign: per-market, the FULL ranked team list with how many
    of the N simulated seasons each team won it, alongside our model %, Kalshi and
    Polymarket. Markets are selectable (World Series / pennant / division /
    playoffs / win-total lines); every team is listed (priced or not) so you can
    search any of them. Reuses futures_edges purely as the book-price source."""
    season = season or str(clock.today_et().year)
    sim = sim or cached(season, n)
    fe = futures_edges(season, sim=sim)
    ns = sim["n_sims"]
    teams = sim["teams"]

    # Book prices keyed for lookup. Winner markets by (type, abbr); win totals by
    # (abbr, line) since each line is its own contract.
    prow, wt_lines = {}, set()
    for r in fe["edges"]:
        if r["type"] == "win_total":
            prow[("win_total", r["abbr"], r.get("line"))] = r
            if r.get("line") is not None:
                wt_lines.add(int(r["line"]))
        else:
            prow[(r["type"], r["abbr"])] = r

    def enrich(t, mtype, pct, count, pr):
        return {
            "team": t["name"], "abbr": t["abbr"], "division": t["division"],
            "league": t["league"], "proj_wins": t.get("proj_wins"),
            "wins": t.get("wins"), "losses": t.get("losses"),
            "count": count, "n": ns, "model_pct": round(pct, 1),
            "kalshi_cents": pr.get("kalshi_cents"), "poly_cents": pr.get("poly_cents"),
            "best_book": pr.get("best_book"), "edge": pr.get("edge"),
            "thin": pr.get("thin", True),
        }

    def winner_market(mtype, key):
        rows = []
        for t in teams:
            pct = t.get(key)
            if pct is None:
                continue
            rows.append(enrich(t, mtype, pct, round(pct / 100.0 * ns),
                               prow.get((mtype, t["abbr"]), {})))
        rows.sort(key=lambda r: r["model_pct"], reverse=True)
        return rows

    markets, order = {}, []
    for mtype, key, label in (("world_series", "p_ws", "World Series champion"),
                              ("pennant", "p_pennant", "Pennant (league champion)"),
                              ("division", "p_division", "Division winner"),
                              ("playoffs", "p_playoffs", "Make the playoffs")):
        markets[mtype] = {"label": label, "group": "Titles", "teams": winner_market(mtype, key)}
        order.append(mtype)

    # Win-total lines: each "L+ wins" is its own selectable market. Kalshi lists a
    # huge ladder (35..115), but most lines are trivial (everyone ~100% or ~0%).
    # Keep only lines that actually discriminate — at least two teams in a live
    # 5-95% band — so the dropdown shows the meaningful band (~80-105).
    for L in (sorted(wt_lines) or [85, 90, 95, 100]):
        rows = []
        for t in teams:
            s = t.get("_wins_sample") or []
            if not s:
                continue
            cnt = sum(1 for w in s if w >= L)
            rows.append(enrich(t, "win_total", 100.0 * cnt / ns, cnt,
                               prow.get(("win_total", t["abbr"], L), {})))
        if sum(1 for r in rows if 5 <= r["model_pct"] <= 95) < 2:
            continue
        rows.sort(key=lambda r: r["model_pct"], reverse=True)
        key = f"win_{L}"
        markets[key] = {"label": f"{L}+ wins", "group": "Season win totals", "teams": rows}
        order.append(key)

    return {"season": season, "n_sims": ns, "n_games_left": sim["n_games_left"],
            "engine": sim.get("engine", "fast"), "markets": markets, "order": order}


def board_cached(season=None, n=4000, ttl=600):
    season = season or str(clock.today_et().year)
    return baseball._cached(("futures_board", season, n), ttl,
                            lambda: futures_board(season, n=n))


def deep_board(agg, season=None):
    """Same board contract as futures_board, but counts come from the deep
    pitch-by-pitch season run (deep_season.run_deep). Book prices are reused from
    the fast-sim futures_edges (prices don't depend on our engine)."""
    season = season or str(clock.today_et().year)
    n = agg.get("n") or 1
    meta = agg["meta"]
    abbr = {}
    try:
        abbr = baseball._abbr_map(season)
    except Exception:
        pass
    # The deep run freezes each team's W-L at run time; overlay TODAY's standings so
    # the record shown is current (the sim can be up to a day old between reruns).
    cur = {}
    try:
        cur = _standings(season)
    except Exception:
        cur = {}
    fe = futures_edges(season)
    prow, wt_lines = {}, set()
    for r in fe["edges"]:
        if r["type"] == "win_total":
            prow[("win_total", r["abbr"], r.get("line"))] = r
            if r.get("line") is not None:
                wt_lines.add(int(r["line"]))
        else:
            prow[(r["type"], r["abbr"])] = r

    def row(tid, count, pr):
        m = meta[tid]
        st = cur.get(tid) or m               # live standings, else the run-time snapshot
        pct = 100.0 * count / n
        best = pr.get("market_cents")
        return {"team": m["name"], "abbr": abbr.get(tid),
                "division": m["division"], "league": m["league"],
                "proj_wins": round(agg["wins_sum"].get(tid, 0) / n, 1),
                "wins": st["wins"], "losses": st["losses"],
                "count": count, "n": n, "model_pct": round(pct, 1),
                "kalshi_cents": pr.get("kalshi_cents"), "poly_cents": pr.get("poly_cents"),
                "best_book": pr.get("best_book"),
                "edge": round(pct - best, 1) if best is not None else None,
                "thin": pr.get("thin", True)}

    markets, order = {}, []
    for mtype, key, label in (("world_series", "ws", "World Series champion"),
                              ("pennant", "pennant", "Pennant (league champion)"),
                              ("division", "division", "Division winner"),
                              ("playoffs", "playoffs", "Make the playoffs")):
        rows = [row(tid, agg[key].get(tid, 0), prow.get((mtype, abbr.get(tid)), {}))
                for tid in meta]
        rows.sort(key=lambda r: r["count"], reverse=True)
        markets[mtype] = {"label": label, "group": "Titles", "teams": rows}
        order.append(mtype)

    for L in (sorted(wt_lines) or [85, 90, 95, 100]):
        rows = []
        for tid in meta:
            hist = agg["wins_hist"].get(tid, {})
            cnt = sum(c for w, c in hist.items() if int(w) >= L)
            rows.append(row(tid, cnt, prow.get(("win_total", abbr.get(tid), L), {})))
        if sum(1 for r in rows if 5 <= r["model_pct"] <= 95) < 2:
            continue
        rows.sort(key=lambda r: r["count"], reverse=True)
        markets[f"win_{L}"] = {"label": f"{L}+ wins", "group": "Season win totals", "teams": rows}
        order.append(f"win_{L}")

    return {"season": season, "n_sims": n, "n_games_left": agg.get("n_games_left"),
            "engine": "deep", "markets": markets, "order": order}


def _winner_markets_winstotal(abbr):
    try:
        return [m for m in kalshi.markets_for_series(f"KXMLBWINS-{abbr}", limit=20)
                if m.get("yes_ask") is not None]
    except Exception:
        return []


def cached(season=None, n=4000, ttl=21600):
    """Daily-ish cached season sim (heavy: ~1,200 games x n sims)."""
    season = season or str(clock.today_et().year)
    return baseball._cached(("season_sim", season, n), ttl, lambda: simulate(season, n))
