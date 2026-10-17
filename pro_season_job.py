"""The NBA and NHL season jobs: the rest of the season played through the real
formats (pro_season) on the BOARDS' validated game probabilities, and the
futures board priced against the series Kalshi actually lists.

Per (home, away) pair the probability is the one the board's headline would
show for that game today:

  NBA  basket.simulate_game on basket.ratings, calibrated
       (calibrate.apply "nba") -- the deep engine carries weight 0 on the
       board (its pre-registered test failed), so it carries none here.
  NHL  (1 - w) x the production model, calibrated, + w x the deep engine,
       w = hockey._DEEP_WEIGHT (0.5, chosen on 2024-25 and confirmed on
       2025-26). The deep engine's own games give the share that goes past
       regulation, who wins those and how many reach the shootout; with no
       deep profiles the league's 2023-26 rates stand in (22.1% of games,
       52.5% to the home side, 32.0% of them shootouts; 3,936 games).

Every ordered pair is priced -- any two clubs can meet in a playoff series --
in one niced child on the server (or the PC's processes), then the Monte
Carlo plays 10,000 seasons from the real standings: finished games banked at
their results, the schedule's remaining games, the NBA Cup's unscheduled
December slots paired inside each conference.

Kalshi's market definitions, read from each series' rules (2026-10-17):
  KXNBAPLAYOFF  the playoffs proper -- the play-in does NOT count
  KXNBAPLAYIN   seeds 7-10 (qualifying for the play-in)
  KXNBASEED     seeds 2-8; 1-6 off the regular season, 7 and 8 off the play-in
  KXNBASEEDOU   "#N seed or better" on the regular-season seed, before the
                play-in
  KXNBARECORD   best / worst record; ties on win % dead-heat at 1/N (the
                simulation breaks such ties at random, which pays the same in
                expectation)
  KXNHLPRES / KXNHLRECORDWORST  the league's own tiebreakers (RW, ROW, W,
                head-to-head, goal difference, goals)
"""
import math
import os
import re
import time
import zlib

import errlog
import pro_season as PS

SEASONS = 10000                  # 3.6 ms a season, either league (measured 2026-10-17)
PROD_N = 2000                    # games per pair for the production model
DEEP_N_SERVER = 150              # NHL deep engine games per pair: 992 pairs x 1.56 ms
DEEP_N_PC = 300                  # = nhl_deep_data.DEEP_N, what a board card uses
# NHL past regulation, 2023-24 to 2025-26 (ESPN, 3,936 games): the fallback
# when the deep engine has no profiles
NHL_OT, NHL_OT_HOME, NHL_SO = 0.2208, 0.5247, 0.3199
GAMES = 82                       # the NBA's season, and the NHL's through 2025-26
_SEED = 20261017


def games_per_club(league, season_end_):
    """The regular season's length: the NHL plays 84 from 2026-27 (the 2025
    CBA; ESPN's 2026-27 schedule has 1,344 games, 84 a club for all 32), 82
    before; the NBA 82."""
    return 84 if league == "nhl" and int(season_end_) >= 2027 else GAMES
# The per-season strength uncertainty for a club rated off its own games:
# sd = TAU x sqrt(K / (K + games played)), K the board's regression constant.
# TAU is research/pro_season_backtest.py's pick (chosen on 2024-25, tested on
# 2025-26 by a rule written first).
# Both chose 0.45, the grid's top (NBA 2025-26 0.06068 vs 0.06340 at 0;
# NHL 0.06036 vs 0.06053 -- a nearly flat grid).
TAU = {"nba": 0.45, "nhl": 0.45}
# What the backtest says each board's season figures are worth, printed on
# the board beside them (research/reports/pro_seasons.md section 2).
SKILL = {"nhl": ("Backtest (2024-25 and 2025-26, the board's model rebuilt each month): the playoff and division figures "
                 "beat the base rate clearly (2025-26 playoffs Brier 0.139 against 0.250); the Cup and finalist figures did "
                 "NOT -- 0.0300 against the base rate's 0.0303 for the Cup. Read the NHL title odds as the format and the "
                 "standings, not as an edge."),
         "nba": ("The level is Kalshi's own win totals, so this board has no backtest of its own (no ladder history "
                 "exists); the model-only version of it beat the base rate on playoffs and divisions but not on the "
                 "title (2025-26 champion Brier 0.0348 against 0.0322).")}
K_REG = {"nba": 12.0, "nhl": 10.0}
# Games a club had played at the backtest's earliest cut (November 1: NBA 71
# and 80 games league-wide, NHL 166 and 180 -- about 5 NBA / 10 NHL a club;
# the conservative of the two)
EARLIEST_CUT_GAMES = 10

# Kalshi's team codes where they differ from ESPN's (every other club is the
# same three letters on both); used only when the code itself is not a club
# in the season's alignment
KALSHI_TO_ESPN = {"nba": {"GSW": "GS", "NOP": "NO", "NYK": "NY", "SAS": "SA", "UTA": "UTAH", "WAS": "WSH"},
                  "nhl": {"UTA": "UTAH"}}


def season_end(today=None):
    """ESPN's label for the season under way: the year it ENDS (2026-27 is
    2027). From August the coming season."""
    import clock
    t = today or clock.today_et()
    return t.year + 1 if t.month >= 8 else t.year


def _get(url, timeout=25):
    import racing
    last = None
    for i in range(3):
        try:
            return racing._get_json(url, timeout=timeout)
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def alignment(league, season_end_, get=None):
    """({abbr: conference}, {abbr: division}, {abbr: display name}) from
    ESPN's standings tree for the season."""
    get = get or _get
    d = get(f"https://site.api.espn.com/apis/v2/sports/{PS._PATH[league]}/standings"
            f"?season={season_end_}&level=3", timeout=25) or {}
    conf, div, names = {}, {}, {}
    for c in d.get("children") or []:
        for dv in c.get("children") or []:
            for e in (dv.get("standings") or {}).get("entries") or []:
                ab = e["team"]["abbreviation"]
                conf[ab] = c["name"]
                div[ab] = dv["name"]
                names[ab] = e["team"].get("displayName") or ab
    return conf, div, names


# ---- the pair probabilities -------------------------------------------------------
def _seed(*parts):
    return zlib.crc32("|".join(map(str, (_SEED,) + parts)).encode())


def _nba_pairs(teams, n_prod):
    import basket
    try:
        import calibrate
        cal = lambda p: max(0.03, min(0.97, calibrate.apply("nba", p)))
    except Exception as e:
        errlog.note("PSJ-nba-cal", e)
        cal = lambda p: p
    rt = basket.ratings("nba")
    out, missing = {}, set()
    for h in teams:
        for a in teams:
            if h == a:
                continue
            rh, ra = rt.get(h), rt.get(a)
            if not rh or not ra:
                missing.update(x for x, r in ((h, rh), (a, ra)) if not r)
                continue
            sim = basket.simulate_game("nba", dict(rh, name=h), dict(ra, name=a), [], [], n=n_prod,
                                       seed=_seed("nba", h, a))
            out[(h, a)] = cal(sim["p_home"])
    return out, {"model": "production, calibrated (deep weight 0)", "missing": sorted(missing),
                 "rated": len(rt)}


def _nhl_deep_counts(job):
    """(home, away, games, home wins, past regulation, home wins of those,
    shootouts) from the deep engine. Runs in the pool or in-process."""
    import random
    import nhl_deep_sim as E
    h, a, hp, ap, n = job
    T = E.tables()
    rng = random.Random(_seed("nhl-deep", h, a))
    hw = ot = otw = so = 0
    for _ in range(n):
        g = E.play_game(hp, ap, rng, T)
        won = g["winner"] == 0
        hw += won
        if g["ot"] or g["so"]:
            ot += 1
            otw += won
            so += 1 if g["so"] else 0
    return h, a, n, hw, ot, otw, so


def _nhl_pairs(teams, n_prod, n_deep, workers=1, season_end_=None):
    import hockey
    import nhl_deep_data as D
    import nhl_pbp as P
    try:
        import calibrate
        cal = lambda p: max(0.03, min(0.97, calibrate.apply("nhl", p)))
    except Exception as e:
        errlog.note("PSJ-nhl-cal", e)
        cal = lambda p: p
    rt = hockey.ratings()
    w = hockey._DEEP_WEIGHT
    profs = None
    try:
        profs = D.current_profiles((season_end_ or season_end()) - 1)
    except Exception as e:
        errlog.note("PSJ-nhl-profiles", e)
    jobs = []
    if profs and w > 0 and n_deep > 0:
        for h in teams:
            for a in teams:
                hp, ap = profs.get(P.from_espn(h)), profs.get(P.from_espn(a))
                if h != a and hp and ap:
                    jobs.append((h, a, hp, ap, n_deep))
    deep = {}
    if jobs:
        if workers > 1:
            import multiprocessing as mp
            with mp.Pool(workers) as pool:
                for r in pool.imap_unordered(_nhl_deep_counts, jobs, chunksize=8):
                    deep[(r[0], r[1])] = r[2:]
        else:
            for j in jobs:
                r = _nhl_deep_counts(j)
                deep[(r[0], r[1])] = r[2:]
    out, missing, n_deep_pairs = {}, set(), 0
    for h in teams:
        for a in teams:
            if h == a:
                continue
            rh, ra = rt.get(h), rt.get(a)
            if not rh or not ra:
                missing.update(x for x, r in ((h, rh), (a, ra)) if not r)
                continue
            sim = hockey.simulate_game(dict(rh, name=h), dict(ra, name=a), [], [], n=n_prod,
                                       seed=_seed("nhl", h, a))
            p = cal(sim["p_home"])
            d = deep.get((h, a))
            if d:
                n, hw, ot, otw, so = d
                n_deep_pairs += 1
                p = (1 - w) * p + w * hw / n
                p_ot = ot / n
                ot_home = otw / ot if ot else NHL_OT_HOME
                so_share = so / ot if ot else NHL_SO
            else:
                p_ot, ot_home, so_share = NHL_OT, NHL_OT_HOME, NHL_SO
            p_ho, p_ao = p_ot * ot_home, p_ot * (1 - ot_home)
            # the regulation share is what is left of the headline figure;
            # a club the overtime split would put below 1% keeps 1%
            p_hr = max(0.01, p - p_ho)
            p_ao = min(p_ao, 1 - p_hr - p_ho - 0.01)
            out[(h, a)] = (p_hr, p_ho, p_ao, so_share)
    return out, {"model": f"production calibrated x {1 - w:g} + deep engine x {w:g}",
                 "deep_pairs": n_deep_pairs, "deep_n": n_deep if jobs else 0,
                 "missing": sorted(missing), "rated": len(rt)}


def p_home(league, pr):
    """The home side's win probability from a pair's entry."""
    return pr if league == "nba" else pr[0] + pr[1]


# ---- the run ------------------------------------------------------------------------
def run(league, n_seasons=SEASONS, workers=1, n_deep=DEEP_N_SERVER, n_prod=PROD_N, seed=None,
        get=None, log=None):
    """The season. Returns the payload the futures board, the team view and
    the job's quality gate read (JSON-able)."""
    t0 = time.time()
    se = season_end()
    get = get or _get
    conf, div, names = alignment(league, se, get)
    games = PS.espn_games(league, se, get=get)
    teams = sorted(conf)
    known = [g for g in games if g["home"] in conf and g["away"] in conf]
    played = [g for g in known if g["final"]]
    remaining = [g for g in known if not g["final"]]
    base = PS.Table(league, conf, div)
    for g in played:
        base.add(g["home"], g["away"], g["hs"], g["as"], g["kind"])
    sched = {t: 0 for t in teams}
    for g in known:
        sched[g["home"]] += 1
        sched[g["away"]] += 1
    # the NBA Cup's knockout nights are on no schedule until group play ends:
    # every club still owed games plays them inside its conference
    G = games_per_club(league, se)
    filler = {t: G - k for t, k in sched.items() if k < G} if league == "nba" else {}
    if log:
        log(f"[season-{league}] {se}: {len(played)} played, {len(remaining)} scheduled, "
            f"{sum(filler.values())} unscheduled slots")
    if league == "nba":
        probs, pmeta = _nba_pairs(teams, n_prod)
    else:
        probs, pmeta = _nhl_pairs(teams, n_prod, n_deep, workers=workers, season_end_=se)
    t1 = time.time()
    if log:
        log(f"[season-{league}] pair probabilities {len(probs)} in {t1 - t0:.0f}s ({pmeta['model']})")
    if len(probs) < len(teams) * (len(teams) - 1):
        return {"league": league, "season_end": se, "n": 0, "generated_at": time.time(),
                "quality": {"ok": False, "why": f"pair probabilities for {len(probs)} of "
                            f"{len(teams) * (len(teams) - 1)} pairs (unrated: {pmeta.get('missing')})"}}
    rem_pairs = [(g["home"], g["away"]) for g in remaining]
    level, anch = {}, None
    if league == "nba":
        try:
            level = market_level(prices("nba", conf))
        except Exception as e:
            errlog.note("PSJ-nba-level", e)
    if level:
        # the NBA's level is Kalshi's win-total ladder at full weight (the
        # NFL board's rule, which its walk-forward chose over a 6/(6+g)
        # blend): the game model gives each pair's shape, the market each
        # club's level, and the ladder's own width the strength uncertainty
        offs, sds, probs = anchor(probs, base, rem_pairs, filler, level)
        noise = {t: sds.get(t, 0.0) for t in teams}
        anch = {"source": "Kalshi KXNBAWINS (mid prices of books within 10c; the ladder's median and IQR, "
                          "the medians scaled to 1,230 wins)",
                "offsets": {t: round(v, 4) for t, v in offs.items()},
                "market": {t: [round(m, 2), round(v, 2), round(mu, 2)] for t, (m, v, mu) in level.items()}}
    else:
        tau = TAU[league]
        noise = {t: tau * math.sqrt(K_REG[league] / (K_REG[league] + base.gp(t))) for t in teams} if tau else None
    seed = _seed("season", league, se, int(time.time() // 86400)) if seed is None else seed
    res = PS.simulate(base, rem_pairs, probs, n_seasons, seed=seed, filler=filler, noise=noise)
    if log:
        log(f"[season-{league}] {n_seasons} seasons in {time.time() - t1:.0f}s")
    rec = {t: {"w": base.w[t], "l": base.l[t], "otl": base.otl[t], "pts": base.pts[t],
               "gf": base.gf[t], "ga": base.ga[t]} for t in teams}
    rows = []
    for g in remaining:
        pr = probs[(g["home"], g["away"])]
        row = {"id": g["id"], "date": g["date"], "home": g["home"], "away": g["away"],
               "p_home": round(p_home(league, pr), 4)}
        if league == "nhl":
            row["p_ot"] = round(pr[1] + pr[2], 4)
        rows.append(row)
    quality = {"ok": True, "teams": len(teams), "played": len(played), "remaining": len(remaining),
               "unscheduled": sum(filler.values()), "schedule_per_team": sorted(set(sched.values())),
               "missing": pmeta.get("missing") or []}
    # a schedule that cannot be the league's (a club short of 82 with no Cup
    # slot to explain it, or past 82) is a broken feed, not a season
    quality["games_per_club"] = G
    if league == "nhl" and any(k != G for k in sched.values()):
        quality.update(ok=False, why=f"NHL schedule per club {quality['schedule_per_team']}, not {G}")
    if league == "nba" and (any(k > G for k in sched.values()) or sum(filler.values()) > 2 * len(teams) + 4):
        quality.update(ok=False, why=f"NBA schedule per club {quality['schedule_per_team']}")
    pmeta["anchor"] = anch
    pmeta["noise_sd"] = ({t: round(v, 4) for t, v in noise.items()} if noise else None)
    return {"league": league, "season_end": se, "n": res["n"], "seed": seed,
            "tally": res["tally"], "hist": res["hist"], "seed_hist": res["seed_hist"],
            "fseed_hist": res.get("fseed_hist") or {}, "max_w": res.get("max_w") or {},
            "conf": conf, "div": div, "names": names, "record": rec, "remaining": rows,
            "model": pmeta, "quality": quality, "generated_at": time.time(),
            "seconds": round(time.time() - t0)}


def publishable(payload):
    return bool(payload and payload.get("n") and (payload.get("quality") or {}).get("ok"))


def run_isolated(league, n_seasons=SEASONS, timeout=3600):
    """run() in a niced child process -- the server's placement: the pair
    pricing is CPU-bound for minutes, and nothing CPU-bound belongs in a
    gunicorn worker on a one-core quota (CLAUDE.md, 2026-09-05). Measured
    2026-10-17, a fresh process, 10,000 seasons: NBA peak 40 MB and 75 s of
    CPU, NHL 68 MB and 302 s (the 992 deep pairs are most of it). The result
    comes back through a temp file, not stdout, so a stray print in any
    imported module cannot corrupt it."""
    import pickle
    import subprocess
    import sys
    import tempfile
    fd, path = tempfile.mkstemp(prefix=f"psj_{league}_", suffix=".pkl")
    os.close(fd)
    try:
        code = ("import os; os.nice(10)\n"
                "import sys, pickle, pro_season_job as J\n"
                f"r = J.run({league!r}, n_seasons={int(n_seasons)})\n"
                "open(sys.argv[1], 'wb').write(pickle.dumps(r))")
        out = subprocess.run([sys.executable, "-c", code, path], cwd=os.path.dirname(os.path.abspath(__file__)),
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout)
        if out.returncode != 0 or not os.path.getsize(path):
            errlog.note(f"PSJ-{league}-child", msg=f"rc={out.returncode}: {out.stderr.decode(errors='replace')[-400:]}")
            return None
        with open(path, "rb") as fh:
            return pickle.load(fh)
    finally:
        try:
            os.remove(path)
        except OSError as e:
            errlog.note(f"PSJ-{league}-tmp", e)


# ---- Kalshi's season markets ---------------------------------------------------------
SERIES = {
    "nba": {"champ": ["KXNBA"], "pennant": ["KXNBAEAST", "KXNBAWEST"],
            "division": ["KXNBAATLANTIC", "KXNBACENTRAL", "KXNBASOUTHEAST",
                         "KXNBANORTHWEST", "KXNBAPACIFIC", "KXNBASOUTHWEST"],
            "playoffs": ["KXNBAPLAYOFF"], "playin": ["KXNBAPLAYIN"],
            "seed1": ["KXNBAEAST1SEED", "KXNBAWEST1SEED"], "seed": ["KXNBASEED"],
            "seedou": ["KXNBASEEDOU"], "record": ["KXNBARECORD"], "wins": ["KXNBAWINS"],
            "mostwins": ["KXNBAMOSTWINS"]},
    "nhl": {"champ": ["KXNHL"], "pennant": ["KXNHLEAST", "KXNHLWEST"],
            "division": ["KXNHLATLANTIC", "KXNHLMETROPOLITAN", "KXNHLCENTRAL", "KXNHLPACIFIC"],
            "playoffs": ["KXNHLPLAYOFF"], "best": ["KXNHLPRES"], "worst": ["KXNHLRECORDWORST"]},
}
_TAIL = re.compile(r"^(\d{2})([A-Z]+?)(\d*)$")
# A ladder rung counts toward the level only when its book is two-sided and
# no wider than this (280 of the 312 NBA win-total rungs on 2026-10-17; the
# rest ran 15c to 99c wide); a board row over it is flagged thin.
MAX_SPREAD = 10.0


def _series_markets(series):
    """Open markets of one series, this season's events only: a series that
    lists next season too keeps the events closing within 200 days of its
    soonest. Cached five minutes."""
    import racing

    def build():
        import kalshi
        rows, cursor = [], ""
        for _ in range(4):
            url = f"{kalshi.BASE}/markets?series_ticker={series}&status=open&limit=1000"
            if cursor:
                url += f"&cursor={cursor}"
            d = kalshi._get_json(url)
            rows += d.get("markets") or []
            cursor = d.get("cursor") or ""
            if not cursor:
                break
        closes = [m.get("close_time") or "" for m in rows if m.get("close_time")]
        if closes:
            import datetime
            first = datetime.datetime.fromisoformat(min(closes).replace("Z", "+00:00"))
            limit = (first + datetime.timedelta(days=200)).isoformat().replace("+00:00", "Z")
            rows = [m for m in rows if (m.get("close_time") or "") <= limit]
        return [{"ticker": m.get("ticker"), "event": m.get("event_ticker") or "",
                 "yes_ask": kalshi._cents(m.get("yes_ask_dollars")),
                 "yes_bid": kalshi._cents(m.get("yes_bid_dollars")),
                 "no_ask": kalshi._cents(m.get("no_ask_dollars")),
                 "volume": m.get("volume")} for m in rows]
    return racing._cached(("psj_series", series), 300, build) or []


def _espn(league, code, conf=()):
    """Kalshi's team code as ESPN spells it THIS season: the code itself when
    it is a club in the alignment (ESPN's Utah NHL code was UTAH in 2024-25
    and is UTA in 2026-27, Kalshi's spelling), else the alias."""
    return code if code in conf else KALSHI_TO_ESPN[league].get(code, code)


def prices(league, conf):
    """{market key: {abbr (or "any" for the league-wide ladder): quote}} off
    Kalshi's tickers -- the team is the ticker's last segment (WINS carries
    it in the event, SEEDOU before the seed number), mapped to ESPN's
    abbreviation. A quote: {cents (YES ask), no_cents, mid, ticker}."""
    out, unmatched = {}, 0

    def put(key, ab, m):
        nonlocal unmatched
        if ab != "any" and ab not in conf:
            unmatched += 1
            return
        if m.get("yes_ask") is None:
            return
        bid = m.get("yes_bid")
        spread = (m["yes_ask"] - bid) if bid is not None else None
        # a mid is read only off a two-sided book within MAX_SPREAD: a 0c bid
        # under a 99c ask "averages" 49.5% (Portland 65+ wins, 2026-10-17)
        mid = (m["yes_ask"] + bid) / 2.0 if spread is not None and spread <= MAX_SPREAD else None
        out.setdefault(key, {})[ab] = {"cents": m["yes_ask"], "no_cents": m.get("no_ask"), "mid": mid,
                                       "spread": spread, "ticker": m["ticker"], "volume": m.get("volume")}
    for kind, series in SERIES[league].items():
        for st in series:
            try:
                rows = _series_markets(st)
            except Exception as e:
                errlog.note("PSJ-prices", e, path=st)
                continue
            for m in rows:
                t = m.get("ticker") or ""
                parts = t.split("-")
                if len(parts) < 3:
                    continue
                ev, last = parts[1], parts[-1]
                if kind in ("champ", "pennant", "division", "playoffs", "seed1", "best", "worst"):
                    put(kind, _espn(league, last, conf), m)
                elif kind == "playin":
                    put("playin", _espn(league, last, conf), m)
                elif kind == "record":
                    sub = "worst" if "WORST" in ev else ("best" if "BEST" in ev else None)
                    if sub:
                        put(sub, _espn(league, last, conf), m)
                elif kind == "seed":
                    mt = re.match(r"^\d{2}(EAST|WEST)(\d+)$", ev)
                    if mt:
                        put(f"seed_{mt.group(1).lower()}_{int(mt.group(2))}", _espn(league, last, conf), m)
                elif kind == "seedou":
                    mt = re.match(r"^([A-Z]+?)(\d+)$", last)
                    if mt:
                        put(f"seedou_{int(mt.group(2))}", _espn(league, mt.group(1), conf), m)
                elif kind == "wins":
                    mt = _TAIL.match(ev)
                    if mt and last.isdigit():
                        put(f"win_{int(last)}", _espn(league, mt.group(2), conf), m)
                elif kind == "mostwins" and last.isdigit():
                    put(f"mostwins_{int(last)}", "any", m)
    out["_unmatched"] = unmatched
    return out


# ---- the NBA level: Kalshi's win totals ---------------------------------------------
def ladder_moments(rungs, games=GAMES):
    """(median, sd, mean) of season wins from a win-total ladder
    {L: P(W >= L)} (mid prices, 0-1). P(W >= k) is the ladder made monotone
    and read linearly between rungs, 1 five wins below the lowest, a
    geometric tail above the highest (nfl_season._market_wins' tail).

    The CENTRE and the WIDTH are what the anchor matches: the median and
    the interquartile range (/1.349), the ladder's tightest books. The mean
    is reported, not matched: the top clubs' ladders are left-skewed (OKC
    2026-10-17: mean 58.6, median ~61.5, P(60+) 64% -- injury risk), a skew
    a symmetric strength draw cannot reproduce, and matching the mean put
    the liquid rungs 14 points off; the near-certain rungs are also capped
    (a 95c bid under the 100c ceiling reads 97.5%)."""
    lines = sorted(rungs)
    if len(lines) < 4:
        return None
    q, run = {}, 1.0
    for L in lines:
        run = min(run, max(0.0, min(1.0, rungs[L])))
        q[L] = run
    pts = [(lines[0] - 5, 1.0)] + [(L, q[L]) for L in lines]
    hi = lines[-1]
    prev = q[lines[-2]]
    r = (q[hi] / prev) ** (1.0 / max(1, hi - lines[-2])) if prev > q[hi] > 0 else 0.5

    def surv(k):
        if k <= pts[0][0]:
            return 1.0
        if k >= hi:
            return q[hi] * (r ** (k - hi))
        for (a, pa), (b, pb) in zip(pts, pts[1:]):
            if a <= k <= b:
                return pa + (pb - pa) * (k - a) / (b - a)
        return 0.0
    s = [surv(k) for k in range(1, games + 1)]
    mean = sum(s)

    def quant(pr):                       # the k where P(W >= k) crosses 1 - pr
        for k in range(1, games + 1):
            if s[k - 1] < 1 - pr:
                return k - 1 + 0.5
        return float(games)
    sd = (quant(0.75) - quant(0.25)) / 1.349
    return quant(0.5), sd, mean


def market_level(px):
    """{abbr: (centre, sd, ladder mean)} from the KXNBAWINS ladder, the
    centres (medians) scaled so the league's wins sum to its games (1,230),
    as the NFL's means are to 272. {} when fewer than 24 clubs have a
    ladder."""
    by = {}
    for key, rows in px.items():
        if not key.startswith("win_"):
            continue
        L = int(key[4:])
        for ab, qt in rows.items():
            if qt.get("mid") is not None:               # two-sided, within MAX_SPREAD
                by.setdefault(ab, {})[L] = qt["mid"] / 100.0
    out = {}
    for ab, rungs in by.items():
        mo = ladder_moments(rungs)
        if mo:
            out[ab] = mo
    if len(out) < 24:
        return {}
    s = sum(m for m, _sd, _mu in out.values())
    scale = GAMES / 2.0 * len(out) / s if s else 1.0
    return {ab: (m * scale, sd, mu) for ab, (m, sd, mu) in out.items()}


def _logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _p_noisy(x, s2):
    """E[logistic(x + e)], e ~ N(0, s2): the probit approximation
    logistic(x / sqrt(1 + pi s2 / 8)), within a point of the exact average
    for the spreads used here."""
    return 1.0 / (1.0 + math.exp(-x / math.sqrt(1.0 + math.pi * s2 / 8.0)))


def anchor(probs, base, remaining, filler, level, iters=60, sd_max=0.8):
    """Each club's logit offset and strength SD so that the season played
    through its schedule gives back the market's ladder: the mean (banked
    wins + its remaining games + half its unscheduled ones) and the width
    (the ladder's SD is the games' own binomial spread plus the strength
    uncertainty's, slope x sd). The means are matched UNDER the noise --
    averaging a logistic over a strength draw pulls every club toward .500,
    and matching them without it left OKC 1.5 wins short of its ladder.
    Returns ({abbr: offset}, {abbr: sd}, the pair table with the offsets in)."""
    teams = list(base.conf)
    d = {t: 0.0 for t in teams}
    sd = {t: 0.0 for t in teams}
    lp = {k: _logit(v) for k, v in probs.items()}
    games = {t: [] for t in teams}
    for h, a in remaining:
        games[h].append((h, a))
        games[a].append((h, a))
    for it in range(iters):
        ew = {t: base.w[t] + 0.5 * filler.get(t, 0) for t in teams}
        slope = {t: 1e-9 for t in teams}
        varg = {t: 0.25 * filler.get(t, 0) for t in teams}
        for h, a in remaining:
            x, s2 = lp[(h, a)] + d[h] - d[a], sd[h] ** 2 + sd[a] ** 2
            p = _p_noisy(x, s2)
            ew[h] += p
            ew[a] += 1 - p
            v = p * (1 - p) / math.sqrt(1.0 + math.pi * s2 / 8.0)
            slope[h] += v
            slope[a] += v
            varg[h] += p * (1 - p)
            varg[a] += p * (1 - p)
        for t in teams:
            if t not in level:
                continue
            d[t] += max(-0.5, min(0.5, (level[t][0] - ew[t]) / slope[t]))
            if it >= 10:                 # the width once the means have settled
                sl = sum(p * (1 - p) for p in (1.0 / (1.0 + math.exp(-(lp[g] + d[g[0]] - d[g[1]])))
                                               for g in games[t]))
                sd[t] = min(sd_max, math.sqrt(max(0.0, level[t][1] ** 2 - varg[t])) / max(sl, 1e-9))
        m = sum(d.values()) / len(d)
        d = {t: x - m for t, x in d.items()}
    shifted = {k: 1.0 / (1.0 + math.exp(-(lp[k] + d[k[0]] - d[k[1]]))) for k in probs}
    return d, sd, shifted


# ---- the futures board ---------------------------------------------------------------
def _conf_short(name):
    return "EAST" if (name or "").lower().startswith("east") else "WEST"


def _model(payload, key):
    """{abbr: model %} for one market key off the run's tallies."""
    n = payload.get("n") or 1
    tal, teams = payload["tally"], sorted(payload["conf"])
    simple = {"champ": "champ", "pennant": "final", "division": "div", "playoffs": "playoffs",
              "playin": "playin", "seed1": "seed1", "best": "best", "worst": "worst"}
    if key in simple:
        return {t: 100.0 * tal[t].get(simple[key], 0.0) / n for t in teams}
    if key.startswith("seed_"):
        _p, c, k = key.split("_")
        hist = payload.get("fseed_hist") or {}
        return {t: 100.0 * (hist.get(t) or {}).get(int(k), (hist.get(t) or {}).get(str(k), 0)) / n
                for t in teams if _conf_short(payload["conf"][t]) == c.upper()}
    if key.startswith("seedou_"):
        k = int(key.split("_")[1])
        return {t: 100.0 * sum(c for s, c in (payload["seed_hist"].get(t) or {}).items() if int(s) <= k) / n
                for t in teams}
    if key.startswith("win_"):
        L = int(key[4:])
        return {t: 100.0 * sum(c for w, c in (payload["hist"].get(t) or {}).items() if int(w) >= L) / n
                for t in teams}
    if key.startswith("mostwins_"):
        L = int(key.split("_")[1])
        return {"any": 100.0 * sum(c for w, c in (payload.get("max_w") or {}).items() if int(w) >= L) / n}
    return {}


_LABELS = {"champ": ("Titles", "Champion"), "pennant": ("Titles", "Conference champion (reaches the Finals)"),
           "division": ("Titles", "Division winner"), "playoffs": ("Titles", "Make the playoffs"),
           "playin": ("Titles", "Make the play-in (seeds 7-10)"), "seed1": ("Seeds", "No. 1 seed in the conference"),
           "best": ("Records", "Best record in the league"), "worst": ("Records", "Worst record in the league")}


def _label(league, key):
    if key in _LABELS:
        g, lbl = _LABELS[key]
        if league == "nhl" and key == "best":
            lbl = "Presidents' Trophy (most points)"
        if league == "nhl" and key == "pennant":
            lbl = "Conference champion (reaches the Stanley Cup Final)"
        return g, lbl
    if key.startswith("seed_"):
        _p, c, k = key.split("_")
        return "Seeds", f"{c.title()} No. {k} seed" + (" (after the play-in)" if int(k) >= 7 else "")
    if key.startswith("seedou_"):
        return "Seeds", f"No. {key.split('_')[1]} seed or better (before the play-in)"
    if key.startswith("win_"):
        return "Season win totals", f"{key[4:]}+ wins"
    if key.startswith("mostwins_"):
        return "Records", f"Any team {key.split('_')[1]}+ wins"
    return "Other", key


def _order_key(key):
    head = ["champ", "pennant", "division", "playoffs", "playin", "seed1", "best", "worst"]
    if key in head:
        return (0, head.index(key), 0)
    if key.startswith("seed_"):
        _p, c, k = key.split("_")
        return (1, 0 if c == "east" else 1, int(k))
    if key.startswith("seedou_"):
        return (2, 0, int(key.split("_")[1]))
    if key.startswith("mostwins_"):
        return (3, 0, int(key.split("_")[1]))
    if key.startswith("win_"):
        return (4, 0, int(key[4:]))
    return (9, 0, 0)


def futures_board(league, payload, px=None):
    """The Featured tab's payload shape (nfl_deep_season.futures_board's):
    every Kalshi market the run can price, each club's model figure, the YES
    and NO asks, and the edges NET of Kalshi's taker fee. A market Kalshi
    does not list is still shown with the model alone."""
    import kalshi
    if not payload or not payload.get("n"):
        return None
    conf, div, names, rec = payload["conf"], payload["div"], payload["names"], payload["record"]
    n = payload["n"]
    if px is None:
        try:
            px = prices(league, conf)
        except Exception as e:
            errlog.note("PSJ-board-prices", e)
            px = {}
    keys = sorted({k for k in px if not k.startswith("_")}
                  | {"champ", "pennant", "division", "playoffs", "best", "worst"}
                  | ({"playin", "seed1"} if league == "nba" else set()), key=_order_key)
    proj = {t: payload["tally"][t].get("pts" if league == "nhl" else "w", 0.0) / n for t in conf}
    anchored = bool((payload.get("model") or {}).get("anchor"))
    markets, order = {}, []
    for key in keys:
        # the win-total ladder IS the anchored board's input: its rows are
        # shown, never edged -- a gap there is the fit's shape (a missing rung
        # stretches an interpolated quartile; a symmetric draw cannot follow a
        # skewed ladder), not a price the market got wrong (Portland "NO on
        # 30+ wins, +10.5" on 2026-10-17 was that)
        fitted = anchored and key.startswith("win_")
        model = _model(payload, key)
        if not model:
            continue
        quotes = px.get(key) or {}
        rows = []
        for ab, pct in model.items():
            q = quotes.get(ab) or {}
            yes, no = q.get("cents"), q.get("no_cents")
            fee_y = kalshi.fee_for_market(q.get("ticker"), yes) if yes and 0 < yes < 100 else 0.0
            fee_n = kalshi.fee_for_market(q.get("ticker"), no) if no and 0 < no < 100 else 0.0
            team = names.get(ab, ab) if ab != "any" else "Any team"
            r = rec.get(ab) or {}
            rows.append({"team": team, "abbr": ab if ab != "any" else None,
                         "division": (div.get(ab) or "").replace(" Division", ""), "league": conf.get(ab),
                         "wins": r.get("w", 0), "losses": r.get("l", 0), "otl": r.get("otl", 0),
                         "proj_wins": round(proj.get(ab, 0.0), 1), "proj_unit": "pts" if league == "nhl" else "W",
                         "count": round(pct / 100.0 * n), "n": n, "model_pct": round(pct, 1),
                         "kalshi_cents": yes, "no_cents": no, "poly_cents": None,
                         "best_book": "Kalshi" if yes is not None else None, "ticker": q.get("ticker"),
                         "volume": q.get("volume"),
                         "edge": (round(pct - yes - fee_y, 1) if yes is not None and 0 < yes < 100 and not fitted
                                  else None),
                         "edge_no": (round(100.0 - pct - no - fee_n, 1) if no is not None and 0 < no < 100 and not fitted
                                     else None),
                         "spread": q.get("spread"),
                         "thin": yes is None or q.get("spread") is None or q["spread"] > MAX_SPREAD})
        rows.sort(key=lambda r: r["model_pct"], reverse=True)
        g, lbl = _label(league, key)
        markets[key] = {"label": lbl + (" (the anchor)" if fitted else ""), "group": g, "teams": rows,
                        "listed": bool(quotes), "fitted": fitted}
        order.append(key)
    m = payload.get("model") or {}
    anch = m.get("anchor")
    note = (f"Every remaining game played through the real {'NHL wildcard and division bracket' if league == 'nhl' else 'NBA play-in and bracket'} "
            f"with the league's own tiebreakers (checked to reproduce ESPN's final standings of 2023-24 to 2025-26 place by place); "
            f"each game at the board's validated probability ({m.get('model')}).")
    if anch:
        note += (" The NBA's team LEVEL is Kalshi's own win-total ladder (as the NFL board's is): the edges here are where Kalshi's "
                 "other markets disagree with its win totals once the format, the schedule and the tiebreakers are played out.")
    # the backtest's earliest cut is November 1, about ten games a club: a
    # board built before the median club has played that many is in a range
    # the backtest never scored (2026-10-17 NHL: 7 games, playoff odds
    # 80-87% for the top clubs against Kalshi's 91-97% -- the regression to
    # average at work, not a measured edge)
    gp = sorted((r.get("w", 0) + r.get("l", 0) + r.get("otl", 0)) for r in rec.values())
    early = not anch and gp and gp[len(gp) // 2] < EARLIEST_CUT_GAMES
    skill = SKILL.get(league)
    if early:
        skill = (f"EARLY SEASON: the median club has played {gp[len(gp) // 2]} games; the backtest's earliest look was at about "
                 f"{EARLIEST_CUT_GAMES}, so these figures are untested here, and the model still rates every club mostly as "
                 f"average -- its edges against Kalshi lean NO on favourites by construction. " + (skill or ""))
    return {"season": payload.get("season_end"), "n_sims": n, "n_games_left": len(payload.get("remaining") or []),
            "skill": skill, "early": bool(early),
            "engine": "season", "sport": league, "markets": markets, "order": order,
            "quality": payload.get("quality"), "generated_at": payload.get("generated_at"),
            "anchored": bool(anch), "noise": (sorted(set((m.get("noise_sd") or {}).values())) or [0])[-1],
            "unmatched": px.get("_unmatched", 0), "note": note}


def team_detail(league, payload, abbr):
    """One club's simulated season: record, projection, the title odds, the
    seed distribution and every remaining game with both sides' chances."""
    if not payload or abbr not in (payload.get("conf") or {}):
        return None
    n = payload["n"] or 1
    tal = payload["tally"][abbr]
    seeds = {int(k): round(100.0 * v / n, 1) for k, v in sorted((payload["seed_hist"].get(abbr) or {}).items(),
                                                                  key=lambda kv: int(kv[0]))}
    sched = []
    for g in payload.get("remaining") or []:
        if abbr not in (g["home"], g["away"]):
            continue
        home = g["home"] == abbr
        p = g["p_home"] if home else 1 - g["p_home"]
        sched.append({"date": g["date"][:10], "opp": g["away"] if home else g["home"], "home": home,
                      "p_win": round(p, 4), "p_ot": g.get("p_ot")})
    r = payload["record"][abbr]
    m = payload.get("model") or {}
    return {"team": payload["names"].get(abbr, abbr), "abbr": abbr, "league": league,
            "conf": payload["conf"][abbr], "div": payload["div"][abbr], "record": r,
            "n_sims": n, "proj": round(tal.get("pts" if league == "nhl" else "w", 0.0) / n, 1),
            "p": {k: round(100.0 * tal.get(k, 0.0) / n, 1)
                  for k in ("playoffs", "playin", "div", "seed1", "best", "worst", "r2", "cf", "final", "champ")
                  if k in tal or k in ("playoffs", "div", "final", "champ")},
            "seeds": seeds, "schedule": sched,
            "anchor": ({"market_wins": (m["anchor"]["market"].get(abbr) or [None])[0],
                        "offset": m["anchor"]["offsets"].get(abbr)} if m.get("anchor") else None),
            "noise_sd": (m.get("noise_sd") or {}).get(abbr)}
