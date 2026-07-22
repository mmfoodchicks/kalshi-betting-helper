"""College football (FBS) deep-season simulator — the college twin of the NFL
season engine.

  DATA   ESPN public JSON: the FBS standings tree (conferences + last-season
         record and point differential) and the week-by-week scoreboard (the
         full upcoming schedule, with completed games locked to their result).
  MODEL  Each team's strength is last season's point differential per game,
         regressed hard toward the mean (college rosters churn year to year via
         recruiting and the transfer portal). A matchup turns two ratings into
         an expected points-for for each side.
  SIM    Every game is played DRIVE BY DRIVE with the shared football engine
         (nfl_game_sim._play_game): alternating possessions with game script,
         short fields and overtime. Across N simulated seasons we tally each
         team's win distribution, then select and run the 12-team College
         Football Playoff (5 highest-ranked conference champions + 7 at-large;
         the four highest-ranked champions get first-round byes) to national
         championship and make-the-Playoff odds.

Priced against Kalshi's college-football futures (KXNCAAF national champion,
KXNCAAFPLAYOFF make-the-Playoff) when those markets are open.
"""

import random
from collections import defaultdict

import clock
import racing

_SITE = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
_WEB = "https://site.web.api.espn.com/apis/v2/sports/football/college-football"

_REG = 0.58           # regress last season's differential toward the mean
_BASE_PPG = 27.5      # FBS average points/game
_HFA_MARGIN = 2.4     # home-field edge in points of margin
_REG_WEEKS = 15       # regular-season scoreboard weeks to sweep


def _season():
    t = clock.today_et()
    return t.year if t.month >= 2 else t.year - 1


# ---- Teams + ratings (FBS standings tree) -----------------------------------
def teams(season=None):
    """{team_id: {name, abbr, location, nick, conf, w, l, diff_pg}} for FBS."""
    season = season or _season()

    def build():
        try:
            d = racing._get_json(f"{_WEB}/standings?season={season - 1}&level=3", timeout=25)
        except Exception:
            return None
        out = {}
        for conf in d.get("children", []):
            cname = conf.get("name", "")
            for div in (conf.get("children") or [conf]):
                for e in div.get("standings", {}).get("entries", []):
                    tm = e.get("team", {})
                    st = {s["name"]: s.get("value") for s in e.get("stats", [])}
                    w = int(st.get("wins") or 0)
                    l = int(st.get("losses") or 0)
                    g = w + l or 12
                    diff = st.get("pointDifferential")
                    if diff is None:
                        diff = (st.get("pointsFor") or 0) - (st.get("pointsAgainst") or 0)
                    out[str(tm.get("id"))] = {
                        "name": tm.get("displayName") or tm.get("name"),
                        "abbr": tm.get("abbreviation"),
                        "location": tm.get("location"),
                        "nick": tm.get("name"),
                        "conf": cname, "w": w, "l": l,
                        "diff_pg": diff / g if g else 0.0}
        return out or None
    return racing._cached(("cfb_teams", season), 24 * 3600, build) or {}


def ratings(season=None):
    """{team_id: rating} in margin points/game — last season's differential
    regressed toward the mean (heavy: college is high-variance year to year)."""
    tm = teams(season)
    if not tm:
        return {}
    mean = sum(t["diff_pg"] for t in tm.values()) / len(tm)
    return {tid: (t["diff_pg"] - mean) * (1 - _REG) for tid, t in tm.items()}


# ---- Schedule (week-by-week scoreboard, results locked) ---------------------
def schedule(season=None):
    """[{home, away, final, home_won}] across the FBS regular season. home/away
    are team ids; games involving a non-FBS opponent carry that side as None."""
    season = season or _season()
    fbs = set(teams(season))

    def build():
        import concurrent.futures as _cf

        def week(wk):
            try:
                return racing._get_json(
                    f"{_SITE}/scoreboard?seasontype=2&week={wk}&dates={season}"
                    "&groups=80&limit=400", timeout=25)
            except Exception:
                return None

        seen = {}
        with _cf.ThreadPoolExecutor(max_workers=8) as ex:
            weeks = list(ex.map(week, range(1, _REG_WEEKS + 1)))
        for d in weeks:
            if not d:
                continue
            for e in d.get("events", []):
                comp = (e.get("competitions") or [{}])[0]
                state = (((comp.get("status") or {}).get("type")) or {}).get("state")
                home = away = None
                hs = as_ = None
                for c in comp.get("competitors", []):
                    tid = str((c.get("team") or {}).get("id") or c.get("id"))
                    try:
                        sc = float(c.get("score"))
                    except (TypeError, ValueError):
                        sc = None
                    if c.get("homeAway") == "home":
                        home, hs = tid, sc
                    else:
                        away, as_ = tid, sc
                if not home or not away:
                    continue
                g = {"id": e.get("id"),
                     "home": home if home in fbs else None,
                     "away": away if away in fbs else None,
                     "final": state == "post"}
                if g["home"] is None and g["away"] is None:
                    continue                      # both non-FBS: ignore entirely
                if g["final"] and hs is not None and as_ is not None:
                    g["home_won"] = hs > as_
                seen[g["id"]] = g
        return list(seen.values())
    return racing._cached(("cfb_sched", season), 12 * 3600, build) or []


# ---- Drive-engine game resolution -------------------------------------------
def _drive_rates(exp_pts):
    import nfl_game_sim
    exp_pts = max(9.0, min(52.0, exp_pts))
    tds = exp_pts * 0.74 / 6.95
    fgm = exp_pts * 0.26 / 3.0
    d = nfl_game_sim._DRIVES
    return (max(0.04, min(0.62, tds / d)),
            max(0.02, min(0.42, fgm / d)),
            0.15)


def _rates_for(ra, rb, host_edge):
    margin = (ra - rb) + _HFA_MARGIN * host_edge
    return _drive_rates(_BASE_PPG + margin / 2.0), _drive_rates(_BASE_PPG - margin / 2.0)


def _play(rh, ra, rng):
    import nfl_game_sim
    g = nfl_game_sim._play_game(rh, ra, rng)
    return g[0]["pts"], g[1]["pts"]


# ---- Season Monte Carlo + 12-team CFP ---------------------------------------
def _conf_champ(conf_members, wins, conf_wins, R, rng):
    """Winner of a conference: most conference wins, then overall rating."""
    return max(conf_members,
               key=lambda t: (conf_wins.get(t, 0), R.get(t, 0.0), rng.random()))


def _cfp_bracket(seeds, R, rng):
    """12-team CFP: seeds 1-4 bye; 5-12 play round one (higher seed hosts);
    then quarters/semis/final at neutral sites. Returns national champion id."""
    if len(seeds) < 12:
        return seeds[0] if seeds else None
    rank = {t: i for i, t in enumerate(seeds)}          # 0 = top seed

    def game(hi, lo, host):
        rh, ra = _rates_for(R.get(hi, 0.0), R.get(lo, 0.0), 1 if host else 0)
        ph, pa = _play(rh, ra, rng)
        return hi if ph > pa else lo

    # Round one: 5v12, 6v11, 7v10, 8v9 (higher seed hosts).
    r1 = [game(seeds[4], seeds[11], True), game(seeds[5], seeds[10], True),
          game(seeds[6], seeds[9], True), game(seeds[7], seeds[8], True)]
    r1.sort(key=lambda t: rank[t])
    # Quarters: 1 vs lowest survivor, 2 vs next, etc. (reseed, neutral).
    byes = [seeds[0], seeds[1], seeds[2], seeds[3]]
    q = [game(byes[0], r1[-1], False), game(byes[1], r1[-2], False),
         game(byes[2], r1[-3], False), game(byes[3], r1[-4], False)]
    q.sort(key=lambda t: rank[t])
    s = [game(q[0], q[-1], False), game(q[1], q[-2], False)]
    s.sort(key=lambda t: rank[t])
    return game(s[0], s[1], False)


def run_season(season=None, n=4000, seed=None, workers=None):
    season = season or _season()
    # Split the seasons across cores when there are enough to be worth it.
    import mp_season
    par = mp_season.run("cfb", "run_season", {"season": season}, n, seed,
                        team_key="team_id",
                        avg_fields=["proj_wins", "champ_pct", "playoff_pct"],
                        sum_fields=["_wins_hist"], workers=workers)
    if par is not None:
        par["teams"].sort(key=lambda r: r["champ_pct"], reverse=True)
        return par
    tm = teams(season)
    R = ratings(season)
    sched = schedule(season)
    if not tm or not R or not sched:
        return None
    conf_of = {tid: t["conf"] for tid, t in tm.items()}
    confs = defaultdict(list)
    for tid, c in conf_of.items():
        if c and "independ" not in c.lower():
            confs[c].append(tid)

    # Locked results + the games left to play (home, away, same_conf).
    base_w = defaultdict(int)
    base_cw = defaultdict(int)                     # conference wins to date
    remaining = []
    played = 0
    for g in sched:
        h, a = g["home"], g["away"]
        same_conf = bool(h and a and conf_of.get(h) and conf_of.get(h) == conf_of.get(a))
        if g["final"] and "home_won" in g:
            played += 1
            if h and a:
                w = h if g["home_won"] else a
                base_w[w] += 1
                if same_conf:
                    base_cw[w] += 1
            elif h or a:                            # FBS beat/lost to non-FBS
                fbs_t = h or a
                if (h and g["home_won"]) or (a and not g["home_won"]):
                    base_w[fbs_t] += 1
            continue
        remaining.append((h, a, same_conf))

    # Each simulated season draws a team "form" offset (margin points) that the
    # preseason rating can't foresee — injuries, breakouts, a freshman QB. It
    # widens the win distributions so make-Playoff and title odds aren't
    # artificially locked for the preseason favorites.
    _FORM_SD = 2.6
    champ = defaultdict(int)
    made = defaultdict(int)
    win_hist = defaultdict(lambda: defaultdict(int))
    rng = random.Random(seed)
    for _ in range(n):
        form = {tid: rng.gauss(0.0, _FORM_SD) for tid in tm}
        wins = defaultdict(int, base_w)
        cwins = defaultdict(int, base_cw)
        for h, a, sc in remaining:
            if not (h and a):                       # FBS vs FCS: 95% hold
                fbs_t = h or a
                if fbs_t and rng.random() < 0.95:
                    wins[fbs_t] += 1
                continue
            rh, ra = _rates_for(R[h] + form[h], R[a] + form[a], +1)
            ph, pa = _play(rh, ra, rng)
            w = h if ph > pa else a
            wins[w] += 1
            if sc:
                cwins[w] += 1
        for tid in tm:
            win_hist[tid][wins.get(tid, 0)] += 1

        # Conference champions, then the 12-team field. The selection metric is
        # résumé-driven (wins) blended with the season's true strength (rating +
        # form) — how the Playoff committee actually ranks.
        champs = [_conf_champ(m, wins, cwins, R, rng) for m in confs.values() if m]
        rankmet = lambda t: (0.6 * wins.get(t, 0) + R.get(t, 0.0) + form.get(t, 0.0))
        champs.sort(key=rankmet, reverse=True)
        top_champs = champs[:5]                     # 5 highest-ranked champs auto-bid
        pool = sorted((t for t in tm if t not in set(top_champs)),
                      key=rankmet, reverse=True)
        at_large = pool[:7]
        field = top_champs + at_large
        # Seeds 1-4 = four highest-ranked conference champions (byes); 5-12 the
        # rest by ranking metric.
        bye_seeds = top_champs[:4]
        rest = sorted([t for t in field if t not in set(bye_seeds)],
                      key=rankmet, reverse=True)
        seeds = bye_seeds + rest
        for t in field:
            made[t] += 1
        nc = _cfp_bracket(seeds, R, rng)
        if nc is not None:
            champ[nc] += 1

    rows = []
    for tid, t in tm.items():
        wh = win_hist[tid]
        tot = sum(wh.values()) or 1
        mean_w = sum(w * c for w, c in wh.items()) / tot
        rows.append({
            "team_id": tid, "name": t["name"], "abbr": t["abbr"],
            "location": t["location"], "nick": t["nick"], "conf": t["conf"],
            "prior": f"{t['w']}-{t['l']}",
            "proj_wins": round(mean_w, 1),
            "champ_pct": round(100.0 * champ[tid] / n, 2),
            "playoff_pct": round(100.0 * made[tid] / n, 1),
            "_wins_hist": dict(wh)})
    rows.sort(key=lambda r: r["champ_pct"], reverse=True)
    return {"season": season, "n_sims": n, "engine": "drive",
            "n_games_left": len([r for r in remaining if r[1] is not None]),
            "games_played": played, "teams": rows}


# ---- Kalshi pricing ---------------------------------------------------------
def _kalshi_prices():
    """{'champ': {name_lc: cents}, 'cfp': {name_lc: cents}} from the open
    college-football futures series."""
    import kalshi

    def series(st):
        out, cursor = {}, None
        for _ in range(6):
            url = f"{kalshi.BASE}/markets?series_ticker={st}&status=open&limit=200"
            if cursor:
                url += f"&cursor={cursor}"
            try:
                d = kalshi._get_json(url)
            except Exception:
                break
            for m in d.get("markets") or []:
                nm = (m.get("yes_sub_title") or "").strip().lower()
                if nm:
                    out[nm] = kalshi._cents(m.get("yes_ask_dollars"))
            cursor = d.get("cursor")
            if not cursor:
                break
        return out
    return {"champ": series("KXNCAAF"), "cfp": series("KXNCAAFPLAYOFF")}


def _match(name_map, team):
    """Best price for a team from a {name_lc: cents} map by location/nick."""
    loc = (team.get("location") or "").lower()
    nick = (team.get("nick") or "").lower()
    name = (team.get("name") or "").lower()
    for key in (name, loc, f"{loc} {nick}", nick):
        if key and key in name_map:
            return name_map[key]
    for k, cents in name_map.items():
        if loc and (loc in k or k in loc) and len(loc) > 3:
            return cents
    return None


_MEMO = {"t": 0.0, "board": None}


def futures_board(season=None, n=4000):
    """National-championship + make-the-Playoff board, priced vs Kalshi. Served
    from the nightly deep-season cache; a cold call computes a smaller run."""
    import deep_cache
    import time as _t
    if _MEMO["board"] and _t.time() - _MEMO["t"] < 1800:
        return _MEMO["board"]
    sim, ts = deep_cache.load("cfb")
    if not sim:
        sim = run_season(season, min(n, 1500))
    if not sim:
        return None
    try:
        px = _kalshi_prices()
    except Exception:
        px = {"champ": {}, "cfp": {}}

    def edge(pct, cents):
        return round(pct - cents, 1) if cents is not None else None

    tmeta = teams(sim["season"])
    champ_rows, cfp_rows = [], []
    for r in sim["teams"]:
        meta = tmeta.get(r["team_id"], r)
        cc = _match(px["champ"], meta)
        pc = _match(px["cfp"], meta)
        champ_rows.append({"team": r["name"], "abbr": r["abbr"], "conf": r["conf"],
                           "proj_wins": r["proj_wins"], "prior": r["prior"],
                           "model_pct": r["champ_pct"], "kalshi_cents": cc,
                           "edge": edge(r["champ_pct"], cc), "thin": cc is None})
        cfp_rows.append({"team": r["name"], "abbr": r["abbr"], "conf": r["conf"],
                         "proj_wins": r["proj_wins"], "prior": r["prior"],
                         "model_pct": r["playoff_pct"], "kalshi_cents": pc,
                         "edge": edge(r["playoff_pct"], pc), "thin": pc is None})
    cfp_rows.sort(key=lambda x: -x["model_pct"])
    board = {"season": sim["season"], "n_sims": sim["n_sims"],
             "engine": sim["engine"], "n_games_left": sim["n_games_left"],
             "games_played": sim["games_played"], "generated_at": ts,
             "markets": {
                 "champ": {"label": "National champion", "teams": champ_rows[:40]},
                 "cfp": {"label": "Make the Playoff (12-team CFP)", "teams": cfp_rows[:40]}},
             "order": ["champ", "cfp"],
             "note": "Drive-level Monte Carlo of every FBS game with a 12-team "
                     "College Football Playoff; national-title and make-the-"
                     "Playoff odds priced against Kalshi's college futures."}
    _MEMO.update(t=_t.time(), board=board)
    return board
