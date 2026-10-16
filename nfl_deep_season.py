"""Multicore deep NFL season: the rest of the schedule AND the playoffs, played
snap by snap with nfl_deep_sim, thousands of times, for every team's title,
seed, division and win-total odds -- the football twin of deep_season.

Each simulated season replays every unfinished regular-season game with the
play-level engine (both teams' unit profiles against each other, home field,
the real neutral-site flag), banks the real results of the games already
played, ranks the standings with the NFL's own tiebreakers (head-to-head,
division record, common games, conference record, strength of victory,
strength of schedule, then the coin -- re-applied from the top each time a
tie shrinks, as the rulebook does), seeds seven a conference, and plays the
bracket: wild card 2v7 3v6 4v5 with the higher seed at home, the 1 seed's bye,
the divisional round with the 1 seed drawing the lowest survivor, the
conference title game, the Super Bowl on a neutral field under postseason
overtime.

Every game of a simulated season draws from its OWN random stream, seeded
from (season seed, game index), so two runs on the same seed play the same
seasons and differencing them isolates whatever changed in between -- the
common-random-numbers trick deep_history relies on for MLB.

Output: per team the counts (playoffs, division, bye, conference, Super
Bowl), the exact-wins histogram, the seed histogram and mean points for and
against; per remaining game both teams' win probabilities, the tie rate, the
mean margin and total; the profiles the run used; and a quality block the
publisher gates on.
"""
import json
import multiprocessing as mp
import os
import random
import time
from collections import defaultdict

import clock
import errlog
import nfl_deep_data as D
import nfl_deep_sim as E
import nfl_pbp as P
import nfl_season

_G = {}

PROGRESS = {"running": False, "done": 0, "total": 0, "started": 0.0, "season": None}
_PROG_DISK = os.path.join(os.environ.get("VIGIL_RUN_DIR") or "/tmp", "vigil-nfl-deep-progress.json")
_PROG_FLUSHED = [0.0]
# Quality gate: the publisher refuses a run whose play-by-play store misses
# more than this share of the season's finished games, or whose profiles lack
# a prior season before week 1 has been played.
MIN_COVERAGE = 0.90


def _prog_flush(final=False):
    now = time.time()
    if not final and now - _PROG_FLUSHED[0] < 1.0:
        return
    _PROG_FLUSHED[0] = now
    try:
        tmp = _PROG_DISK + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(PROGRESS, fh)
        os.replace(tmp, _PROG_DISK)
    except OSError as e:
        errlog.note("NFLDS-prog", e)


def progress_read():
    try:
        with open(_PROG_DISK) as fh:
            p = json.load(fh)
        # a run that died leaves "running" behind; nothing real runs for hours
        if p.get("running") and time.time() - (p.get("started") or 0) > 3 * 3600:
            p["running"] = False
        return p
    except (OSError, ValueError):
        return dict(PROGRESS)


# ---- Schedule and standings -----------------------------------------------------
def schedule_from_store(store):
    """[{id, week, home, away, final, home_pts, away_pts, neutral}] for the
    regular season, from the store's scoreboard events (nfl_pbp.season_records
    fetches all eighteen weeks and merges them by event id)."""
    return P.regular_schedule(store, set(nfl_season._DIV_OF))


def schedule_complete(games):
    """True when every team has its full seventeen-game schedule (banked plus
    remaining). A season sim on a partial schedule plays a different season --
    the old gate could not see it (coverage counts only the events it has)."""
    n = defaultdict(int)
    for g in games:
        n[g["home"]] += 1
        n[g["away"]] += 1
    return len(n) == 32 and all(v == 17 for v in n.values())


class _Season:
    """One simulated season's ledger: results per team for the tiebreakers."""
    __slots__ = ("w", "l", "t", "pf", "pa", "games", "opps", "beaten")

    def __init__(self, teams):
        self.w = {ab: 0.0 for ab in teams}
        self.l = {ab: 0.0 for ab in teams}
        self.t = {ab: 0.0 for ab in teams}
        self.pf = {ab: 0 for ab in teams}
        self.pa = {ab: 0 for ab in teams}
        self.games = {ab: [] for ab in teams}     # (opp, result 1/0/0.5)
        self.opps = {ab: [] for ab in teams}
        self.beaten = {ab: [] for ab in teams}

    def add(self, home, away, hp, ap):
        self.pf[home] += hp; self.pa[home] += ap
        self.pf[away] += ap; self.pa[away] += hp
        if hp > ap:
            rh, ra = 1.0, 0.0
            self.w[home] += 1; self.l[away] += 1
            self.beaten[home].append(away)
        elif ap > hp:
            rh, ra = 0.0, 1.0
            self.w[away] += 1; self.l[home] += 1
            self.beaten[away].append(home)
        else:
            rh = ra = 0.5
            self.t[home] += 1; self.t[away] += 1
        self.games[home].append((away, rh)); self.games[away].append((home, ra))
        self.opps[home].append(away); self.opps[away].append(home)

    def pct(self, ab):
        g = self.w[ab] + self.l[ab] + self.t[ab]
        return (self.w[ab] + 0.5 * self.t[ab]) / g if g else 0.0

    def rec_vs(self, ab, opps):
        """Win pct in games against a set of opponents (None when none played)."""
        n = s = 0.0
        for opp, r in self.games[ab]:
            if opp in opps:
                n += 1; s += r
        return (s / n) if n else None

    def _combined(self, teams):
        """The COMBINED won-lost-tied percentage of a list of clubs (with
        multiplicity: a club beaten twice counts twice) -- the rulebook's
        strength of victory / schedule, not an average of percentages."""
        w = g = 0.0
        for o in teams:
            w += self.w[o] + 0.5 * self.t[o]
            g += self.w[o] + self.l[o] + self.t[o]
        return w / g if g else 0.0

    def sov(self, ab):
        return self._combined(self.beaten[ab])

    def sos(self, ab):
        return self._combined(self.opps[ab])


def _common_opps(group, S):
    sets = [set(S.opps[ab]) for ab in group]
    com = set.intersection(*sets) if sets else set()
    return com - set(group)


def _break(group, S, rng, wildcard):
    """Order a tied group by the NFL tiebreakers, restarting from the first rule
    whenever a team separates (the rulebook's procedure)."""
    group = list(group)
    if len(group) == 1:
        return group
    div_of, conf_of = nfl_season._DIV_OF, nfl_season._CONF_OF

    def h2h(ab):
        others = set(group) - {ab}
        if wildcard and len(group) > 2:
            # Among three or more wild-card clubs head-to-head is a SWEEP rule:
            # it applies only to a club that beat each of the others (it goes
            # first) or lost to each of them (it goes last). The old version
            # took any perfect percentage as a sweep, so a club 1-0 against one
            # rival that had not played the third "swept" them.
            res = {}
            for opp, r in S.games[ab]:
                if opp in others:
                    res.setdefault(opp, []).append(r)
            if len(res) < len(others):
                return 0.0
            if all(all(x == 1.0 for x in rs) for rs in res.values()):
                return 1.0
            if all(all(x == 0.0 for x in rs) for rs in res.values()):
                return -1.0
            return 0.0
        return S.rec_vs(ab, others)

    def div_rec(ab):
        return S.rec_vs(ab, {o for o in nfl_season._DIVS[div_of[ab]] if o != ab})

    def conf_rec(ab):
        return S.rec_vs(ab, {o for o in conf_of if conf_of[o] == conf_of[ab] and o != ab})

    com = _common_opps(group, S)

    def common(ab):
        return S.rec_vs(ab, com)

    # wild card: the common-games step needs four common games for EVERY tied
    # club, otherwise it is skipped for the whole group (it used to rank the
    # short club last instead)
    common_ok = (not wildcard) or all(sum(1 for opp in S.opps[ab] if opp in com) >= 4 for ab in group)

    rules = ([h2h, conf_rec, common, S.sov, S.sos] if wildcard
             else [h2h, div_rec, common, conf_rec, S.sov, S.sos])
    for rule in rules:
        if rule is common and not common_ok:
            continue
        vals = {}
        for ab in group:
            v = rule(ab)
            vals[ab] = -1.0 if v is None else v
        best = max(vals.values())
        if all(v == best for v in vals.values()):
            continue
        winners = [ab for ab in group if vals[ab] == best]
        rest = [ab for ab in group if vals[ab] != best]
        return _break(winners, S, rng, wildcard) + _break(rest, S, rng, wildcard)
    rng.shuffle(group)
    return group


def _rank(teams, S, rng, wildcard):
    """Teams ordered best to worst with tiebreakers applied within equal
    winning percentages."""
    by = defaultdict(list)
    for ab in teams:
        by[round(S.pct(ab), 6)].append(ab)
    out = []
    for pct in sorted(by, reverse=True):
        grp = by[pct]
        if len(grp) == 1:
            out.extend(grp)
            continue
        if wildcard:
            # same-division ties are settled by the division procedure first;
            # only each division's best survivor competes for the spot
            survivors, benched = [], []
            per_div = defaultdict(list)
            for ab in grp:
                per_div[nfl_season._DIV_OF[ab]].append(ab)
            for d, members in per_div.items():
                ordered = _break(members, S, rng, False) if len(members) > 1 else members
                survivors.append(ordered[0]); benched.extend(ordered[1:])
            ordered = _break(survivors, S, rng, True)
            out.append(ordered[0])
            remaining = ordered[1:] + benched
            if remaining:
                out.extend(_rank(remaining, S, rng, True))
        else:
            out.extend(_break(grp, S, rng, False))
    return out


def seed_conference(conf, S, rng):
    """Seven seeds for one conference: four division winners by record, then
    three wild cards."""
    divs = [d for d in nfl_season._DIVS if d.startswith(conf)]
    champs, rest = [], []
    for d in divs:
        order = _rank(list(nfl_season._DIVS[d]), S, rng, wildcard=False)
        champs.append(order[0]); rest.extend(order[1:])
    champs = _rank(champs, S, rng, wildcard=True)
    wc = _rank(rest, S, rng, wildcard=True)[:3]
    return champs + wc


# ---- One simulated season --------------------------------------------------------
def _init_worker(shared):
    _G.clear()
    _G.update(shared)
    _G["T"] = E.tables(shared.get("tables_path"))
    try:
        os.nice(10)
    except (AttributeError, OSError):
        pass


def _play(home, away, grng, neutral=False, playoff=False):
    T = _G["T"]
    prof = _G["profiles"]
    g = E.play_game(prof[home], prof[away], grng, T, neutral=neutral, playoff=playoff)
    return g["h"]["pts"], g["a"]["pts"]


def _sim_one_season(seed):
    rng = random.Random(seed)
    teams = list(nfl_season._DIV_OF)
    S = _Season(teams)
    for g in _G["played"]:
        S.add(g["home"], g["away"], g["home_pts"], g["away_pts"])
    grng = random.Random()
    game_out = []
    for gi, g in enumerate(_G["remaining"]):
        grng.seed(seed * 7919 + gi)
        hp, ap = _play(g["home"], g["away"], grng, neutral=g["neutral"])
        S.add(g["home"], g["away"], hp, ap)
        game_out.append((hp, ap))
    seeds = {}
    conf_champ = {}
    for conf in ("AFC", "NFC"):
        sd = seed_conference(conf, S, rng)
        for i, ab in enumerate(sd):
            seeds[ab] = i + 1
        # wild card: 2v7 3v6 4v5, higher seed hosts
        grng.seed(seed * 7919 + 10000 + (0 if conf == "AFC" else 100))

        def game(hi, lo):
            hp, ap = _play(hi, lo, grng, playoff=True)
            return hi if hp > ap else lo
        w27, w36, w45 = game(sd[1], sd[6]), game(sd[2], sd[5]), game(sd[3], sd[4])
        alive = sorted((w27, w36, w45), key=sd.index)
        d1 = game(sd[0], alive[-1])
        d2 = game(alive[0], alive[1])
        hi, lo = (d1, d2) if sd.index(d1) < sd.index(d2) else (d2, d1)
        conf_champ[conf] = game(hi, lo)
    grng.seed(seed * 7919 + 20000)
    a, b = conf_champ["AFC"], conf_champ["NFC"]
    hp, ap = _play(a, b, grng, neutral=True, playoff=True)
    champ = a if hp > ap else b
    return {"wins": {ab: S.w[ab] + 0.5 * S.t[ab] for ab in teams},
            "pf": dict(S.pf), "pa": dict(S.pa), "seeds": seeds,
            "conf": list(conf_champ.values()), "champ": champ, "games": game_out}


def _new_agg():
    return {"n": 0, "wins_sum": defaultdict(float), "wins_hist": defaultdict(lambda: defaultdict(int)),
            "pf": defaultdict(int), "pa": defaultdict(int), "seed_hist": defaultdict(lambda: defaultdict(int)),
            "playoffs": defaultdict(int), "division": defaultdict(int), "bye": defaultdict(int),
            "conf": defaultdict(int), "sb": defaultdict(int),
            "g_home": defaultdict(int), "g_tie": defaultdict(int), "g_margin": defaultdict(float),
            "g_margin2": defaultdict(float), "g_total": defaultdict(float)}


def _merge_season(agg, s):
    agg["n"] += 1
    for ab, w in s["wins"].items():
        agg["wins_sum"][ab] += w
        agg["wins_hist"][ab][int(w * 2)] += 1          # half-wins (ties) kept exact
        agg["pf"][ab] += s["pf"][ab]; agg["pa"][ab] += s["pa"][ab]
    for ab, sd in s["seeds"].items():
        agg["seed_hist"][ab][sd] += 1
        agg["playoffs"][ab] += 1
        if sd <= 4:
            agg["division"][ab] += 1
        if sd == 1:
            agg["bye"][ab] += 1
    for ab in s["conf"]:
        agg["conf"][ab] += 1
    agg["sb"][s["champ"]] += 1
    for gi, (hp, ap) in enumerate(s["games"]):
        agg["g_home"][gi] += hp > ap
        agg["g_tie"][gi] += hp == ap
        agg["g_margin"][gi] += hp - ap
        agg["g_margin2"][gi] += (hp - ap) ** 2
        agg["g_total"][gi] += hp + ap


def _merge_agg(a, b):
    """Fold a PLAIN (string-keyed, picklable) chunk aggregate into `a`."""
    a["n"] += b["n"]
    for key in ("wins_sum", "pf", "pa", "playoffs", "division", "bye", "conf", "sb"):
        for k, v in b[key].items():
            a[key][k] += v
    for key in ("g_home", "g_tie", "g_margin", "g_margin2", "g_total"):
        for k, v in b[key].items():
            a[key][int(k)] += v
    for key in ("wins_hist", "seed_hist"):
        for ab, h in b[key].items():
            for k, v in h.items():
                a[key][ab][int(k)] += v


def _chunk_seasons(args):
    seed0, k = args
    agg = _new_agg()
    for i in range(k):
        _merge_season(agg, _sim_one_season(seed0 + i))
    # defaultdicts with lambda factories cannot cross the pool boundary
    return _plainify(agg)


def _plainify(agg):
    out = {"n": agg["n"]}
    for key in ("wins_sum", "pf", "pa", "playoffs", "division", "bye", "conf", "sb",
                "g_home", "g_tie", "g_margin", "g_margin2", "g_total"):
        out[key] = {str(k): v for k, v in agg[key].items()}
    for key in ("wins_hist", "seed_hist"):
        out[key] = {ab: {str(k): v for k, v in h.items()} for ab, h in agg[key].items()}
    return out


def default_workers():
    import deep_season
    return deep_season.default_workers()


def run_deep(season=None, n_seasons=2000, workers=None, seed=None, profiles=None, store=None,
             track_progress=True, isolate=True, log=None):
    """The deep NFL season. Returns the aggregate dict (JSON-able) with the
    profiles, the schedule and the quality block attached."""
    season = int(season or clock.today_et().year)

    def _prog(**kw):
        if track_progress:
            PROGRESS.update(**kw)
            _prog_flush()
    _prog(running=True, done=0, total=n_seasons, started=time.time(), season=season)
    try:
        if store is None:
            store = P.season_records(season, log=log)
        T = E.tables()
        if profiles is None:
            profiles, quality = D.build_profiles(season, store=store, T=T, log=log)
        else:
            _d, held = P.coverage(store)
            quality = {"teams": len(profiles), "coverage": round(held / _d, 3) if _d else 0.0,
                       "pbp_games_finished": _d, "pbp_games_held": held, "season": season,
                       "prior_season": True, "teams_with_games": sum(1 for p in profiles.values() if p.get("g"))}
        games = schedule_from_store(store)
        quality["schedule_complete"] = schedule_complete(games)
        quality["schedule_games"] = len(games)
        quality["failed_weeks"] = list(store.get("failed_weeks") or [])
        played = [g for g in games if g["final"] and g["home_pts"] is not None]
        remaining = [g for g in games if not g["final"]]
        shared = {"profiles": profiles, "played": played, "remaining": remaining,
                  "tables_path": E.TABLES_PATH}
        workers = workers or default_workers()
        per = max(1, n_seasons // (workers * 20) or 1)
        seed = random.randrange(1 << 30) if seed is None else int(seed)
        chunks, assigned = [], 0
        while assigned < n_seasons:
            k = min(per, n_seasons - assigned)
            chunks.append((seed + assigned * 9973, k))
            assigned += k
        agg = _new_agg()
        if workers > 1 or isolate:
            with mp.Pool(max(1, workers), initializer=_init_worker, initargs=(shared,)) as pool:
                for part in pool.imap_unordered(_chunk_seasons, chunks):
                    _merge_agg(agg, part)
                    _prog(done=agg["n"])
        else:
            _init_worker(shared)
            for ch in chunks:
                _merge_agg(agg, _chunk_seasons(ch))
                _prog(done=agg["n"])
    finally:
        if track_progress:
            PROGRESS["running"] = False
            _prog_flush(final=True)
    out = _plainify(agg)
    out["season"] = season
    out["seed"] = seed
    out["generated_at"] = time.time()
    out["n_games_left"] = len(remaining)
    out["remaining"] = remaining
    out["played"] = [{k: g[k] for k in ("id", "week", "home", "away", "home_pts", "away_pts")} for g in played]
    out["profiles"] = profiles
    out["quality"] = quality
    out["quality"]["ok"] = publishable(out)
    out["engine"] = {"form_sd": E._FORM_SD, "hfa_yds": E._HFA_YDS,
                     "tables_built": (T.raw.get("fit") or {}).get("built")}
    return out


def publishable(agg):
    """The quality gate the server and the PC both apply before saving: the
    play-by-play behind the profiles covers the finished games, every team has
    its full seventeen-game schedule (a scoreboard week that failed to load
    would otherwise drop its games from the simulated season unseen), and the
    profiles have a prior season or games for every team."""
    q = agg.get("quality") or {}
    if (q.get("coverage") or 0) < MIN_COVERAGE:
        return False
    if not q.get("schedule_complete"):
        return False
    if not q.get("prior_season") and (q.get("teams_with_games") or 0) < 32:
        return False
    return (agg.get("n") or 0) > 0 and len(agg.get("profiles") or {}) == 32


# ---- Boards -------------------------------------------------------------------------
def _record(agg, ab):
    w = l = t = 0
    for g in agg.get("played") or []:
        if ab not in (g["home"], g["away"]):
            continue
        mine = g["home_pts"] if g["home"] == ab else g["away_pts"]
        theirs = g["away_pts"] if g["home"] == ab else g["home_pts"]
        if mine > theirs:
            w += 1
        elif mine < theirs:
            l += 1
        else:
            t += 1
    return w, l, t


def futures_board(agg, season=None):
    """nfl_season.futures_board's payload shape (the Featured tab reuses the
    same UI), from the deep run: titles, conference, division, playoffs, the
    1 seed's bye, and the win-total ladders; Kalshi's futures prices beside."""
    n = agg.get("n") or 1
    season = season or agg.get("season")
    try:
        prices = nfl_season._price_maps()
    except Exception as e:
        errlog.note("NFLDS-prices", e)
        prices = {}

    def enrich(ab, mtype, pct, line=None):
        prof = agg["profiles"].get(ab) or {}
        km = ((prices.get("win_total") or {}).get((ab, line)) if line is not None
              else (prices.get(mtype) or {}).get(ab))
        w, l, t = _record(agg, ab)
        return {"team": prof.get("name") or nfl_season._team_name(ab), "abbr": ab,
                "division": nfl_season._DIV_OF[ab], "league": nfl_season._CONF_OF[ab],
                "proj_wins": round(agg["wins_sum"].get(ab, 0) / n, 1),
                "wins": w, "losses": l, "ties": t,
                "count": round(pct / 100.0 * n), "n": n, "model_pct": round(pct, 1),
                "kalshi_cents": km, "poly_cents": None,
                "best_book": "Kalshi" if km is not None else None,
                "edge": round(pct - km, 1) if km is not None else None, "thin": km is None,
                "level": prof.get("level")}

    markets, order = {}, []
    for mtype, key, label in (("world_series", "sb", "Super Bowl champion"),
                              ("pennant", "conf", "Conference champion (AFC/NFC)"),
                              ("division", "division", "Division winner"),
                              ("bye", "bye", "No. 1 seed (first-round bye)"),
                              ("playoffs", "playoffs", "Make the playoffs")):
        rows = [enrich(ab, mtype, 100.0 * agg[key].get(ab, 0) / n) for ab in nfl_season._DIV_OF]
        rows.sort(key=lambda r: r["model_pct"], reverse=True)
        markets[mtype] = {"label": label, "group": "Titles", "teams": rows}
        order.append(mtype)
    for L in range(6, 15):
        rows = []
        for ab in nfl_season._DIV_OF:
            hist = agg["wins_hist"].get(ab) or {}
            tot = sum(hist.values()) or 1
            pct = 100.0 * sum(c for hw, c in hist.items() if int(hw) >= 2 * L) / tot
            rows.append(enrich(ab, "win_total", pct, line=L))
        if sum(1 for r in rows if 5 <= r["model_pct"] <= 95) < 4:
            continue
        rows.sort(key=lambda r: r["model_pct"], reverse=True)
        key = f"win_{L}"
        markets[key] = {"label": f"{L}+ wins", "group": "Season win totals", "teams": rows}
        order.append(key)
    return {"season": season, "n_sims": n, "n_games_left": agg.get("n_games_left"),
            "engine": "deep", "sport": "nfl", "markets": markets, "order": order,
            "quality": agg.get("quality"), "generated_at": agg.get("generated_at"),
            "note": ("Play-by-play engine: every remaining game snap by snap, both units against "
                     "each other, NFL tiebreakers, the full bracket. Levels anchored to Kalshi's "
                     "win totals at 6/(6+games played); the unit rates are the season's own "
                     "play-by-play shrunk toward last year's.")}


def game_rows(agg, abbr=None):
    """Per remaining game: both teams' win probabilities and the score shape."""
    n = agg.get("n") or 1
    out = []
    for gi, g in enumerate(agg.get("remaining") or []):
        if abbr and abbr not in (g["home"], g["away"]):
            continue
        k = str(gi)
        hw = agg["g_home"].get(k, 0) / n
        tie = agg["g_tie"].get(k, 0) / n
        mm = agg["g_margin"].get(k, 0.0) / n
        m2 = agg["g_margin2"].get(k, 0.0) / n
        # rounded so the three ALWAYS sum to one (1/3 + 1/3 + 1/3 did not)
        p_home, p_tie = round(hw, 4), round(tie, 4)
        out.append({"id": g["id"], "week": g["week"], "home": g["home"], "away": g["away"],
                    "neutral": g.get("neutral", False),
                    "p_home": p_home, "p_away": round(1 - p_home - p_tie, 4), "p_tie": p_tie,
                    "exp_margin": round(mm, 2), "sd_margin": round(max(0.0, m2 - mm * mm) ** 0.5, 2),
                    "exp_total": round(agg["g_total"].get(k, 0.0) / n, 2)})
    return out


def team_detail(agg, abbr, season=None):
    """One team from the deep run: record, projected wins and their spread,
    seeds, titles, every remaining game with BOTH sides' probabilities, the
    unit profile beside the league, and the player lines nfl_season keeps."""
    n = agg.get("n") or 1
    ab = nfl_season._canon(abbr)
    prof = (agg.get("profiles") or {}).get(ab)
    if not prof:
        return None
    w, l, t = _record(agg, ab)
    hist = agg["wins_hist"].get(ab) or {}
    wins_hist = {}
    for hw, c in hist.items():
        key = str(int(hw) / 2 if int(hw) % 2 else int(hw) // 2)
        wins_hist[key] = wins_hist.get(key, 0) + c
    seeds = {str(k): round(100.0 * v / n, 1) for k, v in sorted((agg["seed_hist"].get(ab) or {}).items(), key=lambda kv: int(kv[0]))}
    sched = []
    for g in agg.get("played") or []:
        if ab in (g["home"], g["away"]):
            home = g["home"] == ab
            mine = g["home_pts"] if home else g["away_pts"]; theirs = g["away_pts"] if home else g["home_pts"]
            sched.append({"week": g["week"], "opp": g["away"] if home else g["home"], "home": home,
                          "final": True, "result": "W" if mine > theirs else "L" if mine < theirs else "T",
                          "score": f"{mine}-{theirs}"})
    for r in game_rows(agg, ab):
        home = r["home"] == ab
        sched.append({"week": r["week"], "opp": r["away"] if home else r["home"], "home": home,
                      "neutral": r["neutral"], "final": False,
                      "p_win": r["p_home"] if home else r["p_away"],
                      "p_loss": r["p_away"] if home else r["p_home"], "p_tie": r["p_tie"],
                      "exp_margin": r["exp_margin"] if home else -r["exp_margin"],
                      "exp_total": r["exp_total"]})
    sched.sort(key=lambda s: s["week"])
    try:
        players = (nfl_season.team_detail(ab, season or agg.get("season")) or {}).get("players") or []
    except Exception as e:
        errlog.note("NFLDS-players", e)
        players = []
    return {"team": prof.get("name") or nfl_season._team_name(ab), "abbr": ab,
            "season": season or agg.get("season"), "engine": "deep", "n_sims": n,
            "wins": w, "losses": l, "ties": t,
            "proj_wins": round(agg["wins_sum"].get(ab, 0) / n, 2), "wins_hist": wins_hist,
            "exp_pf": round(agg["pf"].get(ab, 0) / n, 1), "exp_pa": round(agg["pa"].get(ab, 0) / n, 1),
            "p_playoffs": round(100.0 * agg["playoffs"].get(ab, 0) / n, 1),
            "p_division": round(100.0 * agg["division"].get(ab, 0) / n, 1),
            "p_bye": round(100.0 * agg["bye"].get(ab, 0) / n, 1),
            "p_conf": round(100.0 * agg["conf"].get(ab, 0) / n, 1),
            "p_sb": round(100.0 * agg["sb"].get(ab, 0) / n, 1),
            "seeds": seeds, "schedule": sched, "units": D.summary(prof), "players": players,
            "source": "play-by-play deep engine; player lines from Sleeper projections + real stats to date"}
