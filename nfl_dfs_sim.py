"""NFL per-game correlated Monte Carlo, seeded by Sleeper's weekly projections.

Sleeper publishes per-week projections that carry BOTH fantasy points and the
component stats behind them (pass/rush/rec yards, TDs, receptions), and every
row is tagged with the game it belongs to. That lets us stop treating each
player as an isolated projection and instead *simulate the game*: draw a shared
game environment (pace/total), a game script (who's ahead -> RB carries vs.
garbage-time passing), and a per-team passing latent that a QB shares with his
own pass-catchers. The result is a fantasy-point DISTRIBUTION per player -- floor
/ median / ceiling / boom-rate -- with the correlations best-ball and DFS live
on (a QB + his WR boom together; a game shootout lifts everyone).

Marginals stay centred on Sleeper's projection (every scaling factor is mean-1 /
mean-0), so the sim only adds shape + correlation, it doesn't invent new means.
"""

import urllib.request
import clock
import dk_scoring
import errlog
import json as _json
import gzip as _gzip
import math as _math
import random as _random
import time as _time
import threading as _threading

_PROJ = "https://api.sleeper.com/projections/nfl/{season}/{week}"
_cache = {}


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                               "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=25) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = _gzip.decompress(data)
    return _json.loads(data)


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and _time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    if val is not None:
        _cache[key] = (_time.time(), val)
    return val


# ---- PPR scoring ------------------------------------------------------------
def _ppr(pass_yd, pass_td, ints, rush_yd, rush_td, rec, rec_yd, rec_td, fum):
    """DraftKings NFL Classic scoring for one SIMULATED stat line.

    Two things were wrong here, and both mattered because this function scores
    the sample arrays the optimizer and the contest sim actually run on:

    - Interceptions and lost fumbles were -2. DraftKings pays -1. The projection
      shown next to each player already used -1 (dk_points/_DK), so the number on
      screen and the number the lineup was built from disagreed.
    - The yardage bonuses were missing entirely. DK pays +3 for 300 passing, 100
      rushing or 100 receiving yards, and they are not a rounding error: a
      workhorse back clears 100 often enough that the bonus is a real part of his
      distribution, and it is worth more to a boom game than a whole reception.
      Applied per SAMPLE (not to the mean), so a player who averages 85 yards
      collects it exactly as often as he clears the line."""
    s = dk_scoring.NFL_OFF
    pts = (pass_yd * s["pass_yd"] + pass_td * s["pass_td"] + ints * s["int"]
           + rush_yd * s["rush_yd"] + rush_td * s["rush_td"]
           + rec * s["rec"] + rec_yd * s["rec_yd"] + rec_td * s["rec_td"]
           + fum * s["fumble_lost"])
    if pass_yd >= 300:
        pts += s["pass_300"]
    if rush_yd >= 100:
        pts += s["rush_100"]
    if rec_yd >= 100:
        pts += s["rec_100"]
    return pts


# ---- Weekly projections (Sleeper), grouped by game --------------------------
_POS = ("QB", "RB", "WR", "TE")


def weekly_games(season, week):
    """{game_id: {label, players:[{name,pos,team,opp, means:{...}, proj_pts}]}} for a
    week's skill players, from Sleeper. Cached 1h."""
    def build():
        q = "&".join(f"position[]={p}" for p in _POS)
        url = f"{_PROJ.format(season=season, week=week)}?season_type=regular&{q}&order_by=pts_ppr"
        try:
            rows = _get(url)
        except Exception as _e:
            errlog.note("NFLDFS-sleeper-fetch", _e)
            return None
        games = {}
        for r in rows:
            st = r.get("stats") or {}
            pts = st.get("pts_ppr")
            if not pts or pts <= 0:
                continue
            p = r.get("player") or {}
            gid = r.get("game_id")
            means = {"pass_yd": st.get("pass_yd", 0.0) or 0.0,
                     "pass_td": st.get("pass_td", 0.0) or 0.0,
                     "int": st.get("pass_int", 0.0) or 0.0,
                     "rush_yd": st.get("rush_yd", 0.0) or 0.0,
                     "rush_td": st.get("rush_td", 0.0) or 0.0,
                     "rec": st.get("rec", 0.0) or 0.0,
                     "rec_yd": st.get("rec_yd", 0.0) or 0.0,
                     "rec_td": st.get("rec_td", 0.0) or 0.0,
                     "fum": st.get("fum_lost", 0.0) or 0.0,
                     # the volume components the reconciliation (nfl_recon)
                     # and the constrained simulator read; the legacy loop
                     # above never looks at them
                     "pass_att": st.get("pass_att", 0.0) or 0.0,
                     "pass_cmp": st.get("pass_cmp", 0.0) or 0.0,
                     "rec_tgt": st.get("rec_tgt", 0.0) or 0.0,
                     "rush_att": st.get("rush_att", 0.0) or 0.0}
            games.setdefault(gid, {"players": [], "label": None})["players"].append({
                "name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
                "pos": p.get("position"), "team": r.get("team"), "opp": r.get("opponent"),
                "means": means, "proj_pts": round(pts, 2)})
        for gid, g in games.items():
            teams = []
            for pl in g["players"]:
                if pl["team"] not in teams:
                    teams.append(pl["team"])
            g["teams"] = teams
            g["label"] = f"{teams[1]} @ {teams[0]}" if len(teams) >= 2 else (teams[0] if teams else "?")
        # Stage 2C: every player also carries `recon`, his line with the
        # team's passing books balanced (nfl_recon), and every game a
        # per-team report. `means` is untouched: the legacy simulator keeps
        # reading it, so a board built here does not change; the constrained
        # simulator reads `recon`. A failure here leaves the games as they
        # were, with the failure in the ledger.
        try:
            import nfl_recon
            nfl_recon.reconcile_games(games)
        except Exception as _e:
            errlog.note("RECON-weekly", _e)
        return games or None
    return _cached(("nfl_sleeper", season, week), 3600, build)


# DraftKings scoring, for turning a MEAN stat line into a projected-points seed.
# Read from the shared table so it cannot drift from _ppr (it had: this said -1
# for turnovers while _ppr, which scores the simulations the optimizer actually
# runs on, charged -2). The 300/100/100 bonuses are deliberately absent here:
# they are threshold events, and a mean cannot say how often a 85-yard-average
# back clears 100. _ppr applies them per SAMPLE, which is where they belong.
_DK = {"pass_yd": dk_scoring.NFL_OFF["pass_yd"], "pass_td": dk_scoring.NFL_OFF["pass_td"],
       "int": dk_scoring.NFL_OFF["int"], "rush_yd": dk_scoring.NFL_OFF["rush_yd"],
       "rush_td": dk_scoring.NFL_OFF["rush_td"], "rec": dk_scoring.NFL_OFF["rec"],
       "rec_yd": dk_scoring.NFL_OFF["rec_yd"], "rec_td": dk_scoring.NFL_OFF["rec_td"],
       "fum": dk_scoring.NFL_OFF["fumble_lost"]}


def dk_points(means):
    return sum(_DK[k] * (means.get(k) or 0.0) for k in _DK)


def preseason_games(season, week):
    """weekly_games' shape for exhibitions, where Sleeper projects nothing.

    Every stat line is built the same way the preseason game engine builds one:
    the measured team-game budget from 96 exhibitions, distributed by the
    INVERTED usage model, and scaled to what Kalshi's ladder says the game is
    worth. So a DFS lineup and a combo slip on the same board are reading the
    same projection rather than two that happen to look similar."""
    def build():
        import nfl_live, nfl_preseason, kalshi_nfl, nfl_game_sim
        try:
            sched = nfl_live.schedule(week, int(season), seasontype=1) or []
        except Exception as _e:
            errlog.note("NFLDFS-espn-schedule", _e)
            return None
        try:
            ros = nfl_preseason.rosters(season) or {}
        except Exception as _e:
            errlog.note("NFLDFS-rosters", _e)
            return None
        try:
            idx = kalshi_nfl.index()
        except Exception as _e:
            errlog.note("NFLDFS-kalshi-index", _e)
            idx = {}
        games = {}
        for gm in sched:
            h, a = gm.get("home"), gm.get("away")
            if not h or not a:
                continue
            # Kalshi's number where there is one; the measured league average
            # where there is not, so a game with no market still gets a lineup.
            pts = {h: nfl_preseason.PRE_TEAM["points"], a: nfl_preseason.PRE_TEAM["points"]}
            props = {}
            suffix = nfl_game_sim._suffix_for(idx, h, a)
            if suffix:
                try:
                    imp = kalshi_nfl.implied(suffix) or {}
                    props = kalshi_nfl.prop_ladders(suffix) or {}
                except Exception:
                    imp = {}
                tot = float(imp.get("total") or 0) or None
                if tot:
                    m = float(imp.get("margin") or 0.0)
                    fav = imp.get("favourite")
                    edge = m if fav == h else (-m if fav == a else 0.0)
                    pts = {h: max(6.0, (tot + edge) / 2.0), a: max(6.0, (tot - edge) / 2.0)}
            rows = []
            for ab, opp in ((h, a), (a, h)):
                names = {nfl_preseason._key(p["name"]) for p in (ros.get(ab) or [])}
                mine = {k: v for k, v in props.items() if k[1] in names}
                prof = nfl_game_sim.profile_from_points(ab, ab, pts[ab], ab == h,
                                                        ros.get(ab), mine)
                for pl in prof["players"]:
                    means = {"pass_yd": pl["pass_yd"], "pass_td": pl["pass_td"],
                             "int": pl.get("pass_int", 0.0), "rush_yd": pl["rush_yd"],
                             "rush_td": pl["rush_td"], "rec": pl["rec"],
                             "rec_yd": pl["rec_yd"], "rec_td": pl["rec_td"],
                             "fum": 0.0}
                    rows.append({"name": pl["name"], "pos": pl["pos"], "team": ab,
                                 "opp": opp, "means": means,
                                 "proj_pts": round(dk_points(means), 2),
                                 "note": pl.get("note")})
            if rows:
                games[f"pre-{a}@{h}"] = {"players": rows, "teams": [h, a],
                                         "label": f"{a} @ {h}"}
        return games or None
    return _cached(("nfl_pre_dfs", season, week), 3600, build)


# ---- Correlated game simulation --------------------------------------------
# How much each latent swings a stat (mean-1 multiplicative unless noted).
_ENV_SD = 0.18          # shared pace/total: a shootout lifts both teams' volume
_QB_SD = 0.26           # a team's passing day -- QB shares it with his WR/TE
_RUSH_SD = 0.24         # rusher-specific volume noise
_SCRIPT_SD = 1.0        # game script; + = team ahead (more rush, less late pass)
_SCRIPT_RUSH = 0.14     # leading -> more rush yards
_SCRIPT_PASS = 0.12     # trailing -> garbage-time pass volume
_TD_SD = 0.0            # TDs handled as Poisson, no extra multiplier

# What produced a board's point arrays, stamped on every tournament artifact
# so two boards built by different simulators can never be confused: this
# is the production model; the constrained simulator (a separate mode, in
# research) will stamp its own name, version and fitted parameters.
SIM_MODEL = "legacy-latent"
SIM_MODEL_VERSION = 1


# 1 covered the offense only, and a promotion-readiness audit found the kicker
# still multiplied and the defense still shifted by a fraction -- 47% and 44% of
# their scores unprintable. 2 pins the kicker on its field-goal rate and spends
# the defense's fractional shift as a coin, so the WHOLE showdown pool is legal.
DISCRETE_VERSION = 2      # showdown-only discrete legacy scorer; 0 means off


# Showdown serves the discrete scorer; Classic never does. One constant, so the
# decision is greppable and reversible in one line.
#
# Promoted after the readiness study in showdown_audit_report.md sections
# Y3-Y6, against five criteria and a Monte Carlo control for every one of them.
# The case is CORRECTNESS, not money: a simulated score that DraftKings cannot
# print is wrong as a model of DraftKings, and under legacy not one of 26
# offensive scores recomputed from its own stat line while 54.2% of them put
# their captain's 1.5x somewhere the engine's 0.01 tie bucket cannot hold it.
# What it cost: player means move at most 0.21 DK points, the worst correlation
# move between two rosterable players is 0.021 against a legacy-vs-legacy noise
# maximum of 0.019, the top twenty reorders LESS than two legacy runs do
# (4 of 20 slots identical against the control's 2 of 20), captain exposure
# shifts by at most one entry of twenty, and scoring costs 3% more time at flat
# memory. The money gate stays SHUT: a legal support says nothing about the
# field model.
SD_DISCRETE = True


def sim_stamp(n=None, preseason=False, discrete=False):
    """{model, version, params, ...}: enough to say which model, with which
    constants, generated a board's worlds. The game loop draws from the
    module RNG unseeded, so a build is reproducible in distribution, not
    draw for draw; the stamp says so rather than implying otherwise."""
    return {"model": (SIM_MODEL + "-discrete" if discrete else SIM_MODEL),
            "version": SIM_MODEL_VERSION,
            "discrete_version": (DISCRETE_VERSION if discrete else 0),
            "projections": "sleeper-weekly",
            "params": {"env_sd": _ENV_SD, "qb_sd": _QB_SD, "rush_sd": _RUSH_SD, "script_sd": _SCRIPT_SD,
                       "script_rush": _SCRIPT_RUSH, "script_pass": _SCRIPT_PASS, "td_sd": _TD_SD},
            "touchdowns": "poisson, independent per player",
            "receiving_noise": "none beyond the shared game, team-passing and script factors",
            "marginals": ("component means scaled BEFORE the draw so the mean lands on Sleeper "
                          "with whole yards and catches; no multiply touches a finished score"
                          if discrete else
                          "each player's points rescaled to the Sleeper mean after the loop"),
            "lattice": ("every score recomputes from integer components through DraftKings' own "
                        "scorer -- offense, kicker and defense alike -- so all of them are "
                        "reachable (research/sd_support)" if discrete else
                        "scores sit on a 0.01 grid; DK offence lives on 0.02 (research/sd_lattice)"),
            "dst": ("components of the opposing offense in the same world, shifted to the "
                    "Sleeper mean by whole points plus a coin for the fraction"
                    if discrete else
                    "components of the opposing offense in the same world, shifted to the Sleeper mean"),
            "kicker": ("extra points off the offense's own touchdowns, field goals at a Poisson "
                       "mean solved so the level lands on the projection"
                       if discrete else
                       "extra points and field goals off the offense, then rescaled to the projection"),
            "seed": None, "n": (int(n) if n else None), "preseason": bool(preseason)}


_CAL = []          # re-entrancy flag: the pin's own probe must not re-calibrate


def _pois(mean, rng=_random):
    """Poisson draw (small means) via inversion. `rng` is anything with
    random(): the module generator by default (the legacy model is
    unseeded), a random.Random(seed) for the constrained model's
    reproducible defenses and kickers."""
    if mean <= 0:
        return 0
    import math
    L, k, p = math.exp(-mean), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


# Prop components we expose (stat key -> (label, line rounding step, min mean to offer)).
# Floors keep the board to players a book would actually post a line on.
_PROP_SPECS = [("pass_yd", "pass yds", 5, 180), ("rush_yd", "rush yds", 5, 30),
               ("rec_yd", "rec yds", 5, 30), ("rec", "receptions", 0.5, 3.0)]
# The "is this worth posting a line on" floors above are regular-season sized and
# post NOTHING in August: a preseason quarterback projects around 100 yards
# against a 180 cutoff, so the whole Pick 6 board came back empty. These are the
# same idea against the measured exhibition budget -- roughly a 40% game.
_PRE_PROP_FLOOR = {"pass_yd": 40.0, "rush_yd": 12.0, "rec_yd": 12.0, "rec": 1.0}


def _prop_line(mean, step):
    """A real book-style half-point line near the projection."""
    import math
    if step >= 1:                          # yards: snap to a round number, minus .5
        return round(mean / step) * step - 0.5
    return math.floor(mean) + 0.5          # receptions: X.5


def simulate_game(game, n=4000, with_samples=False, preseason=False, discrete=False,
                  with_components=False):
    """Correlated MC of one game. Returns per-player fantasy-point distributions,
    correlation-aware component prop over/unders, and QB->receiver stacks.
    with_samples=True attaches each player's rescaled point array (`arr`) so a DFS
    contest sim can score whole lineups with the within-game correlation intact.

    discrete=True is the SHOWDOWN-ONLY experimental mode (nfl_dfs_sim.DISCRETE
    _VERSION). It changes two things and nothing else:

      * yards and receptions are drawn on their real integer support before
        scoring, instead of staying continuous. Touchdowns, interceptions and
        fumbles were always integers (Poisson), and `_ppr` was always the
        exact DraftKings scorer, so the ONLY reason scores were landing off
        the legal lattice was that the yardage feeding that scorer was not
        whole;
      * the mean is pinned UPSTREAM, by scaling each player's component means
        before the draw, rather than by multiplying his finished point array
        by proj/raw afterwards. The old multiply is what pushed every score
        off the lattice, and no amount of rounding afterwards can undo it.

    The latent structure -- the shared game factor, the quarterback latent,
    the opposed scripts -- is untouched, so this is the same football model
    with a legal support, not a new one. Default False: the classic path is
    byte-identical to before this existed.

    with_components=True attaches each player's per-world stat line under
    `comps` -- the nine numbers `_ppr` is paid on. It exists so an auditor can
    recompute every score from the box score that produced it instead of
    trusting that it was computed correctly (research/sd_support). It is
    RESEARCH-ONLY and costs real memory: nine arrays a player, so a 55-man
    showdown at 20,000 worlds is about 150 MB. Nothing in production passes
    it, and the guard suite pins that."""
    players = game["players"]
    teams = game.get("teams") or []
    # The upstream mean pin. The old code let the simulation run at the raw
    # component means and then multiplied each finished point array by
    # proj/raw -- which is what pushed the scores off the legal lattice. Here
    # the correction goes in BEFORE the draw: a short calibration run measures
    # each player's raw mean, and his component means are scaled so the mean
    # lands on the projection with the scores still whole.
    #
    # Points are close to linear in this scale (yards and receptions exactly,
    # touchdowns through the Poisson mean), so one Newton-ish step on the
    # ratio converges; the yardage bonuses are the only nonlinearity and they
    # are small. What is left over is reported as pin_err rather than
    # corrected by a multiply, because a multiply is the bug.
    pin = [1.0] * len(players)
    if discrete and not _CAL:
        cal = max(400, min(2000, n // 4))
        base = simulate_game(game, n=cal, with_samples=False, preseason=preseason,
                             discrete=False)
        raw_by = {r["name"]: r.get("sim_mean_raw") for r in base["players"]}
        for i, pl in enumerate(players):
            raw = raw_by.get(pl["name"]) or 0.0
            proj = float(pl.get("proj_pts") or 0.0)
            pin[i] = (proj / raw) if raw > 0 and proj > 0 else 1.0
        # A second pass, this one DISCRETE, so the refinement sees the
        # rounding it has to live with. Continuous linearity sizes the first
        # step; only a discrete run can tell how far integer yards and whole
        # catches actually move a small projection.
        try:
            _CAL.append(1)
            probe = simulate_game({**game, "_pin": list(pin)}, n=cal, with_samples=False,
                                  preseason=preseason, discrete=True)
            got = {r["name"]: r["sim_mean_raw"] for r in probe["players"]}
            for i, pl in enumerate(players):
                have, proj = got.get(pl["name"]) or 0.0, float(pl.get("proj_pts") or 0.0)
                if have > 0 and proj > 0:
                    pin[i] *= proj / have
        finally:
            _CAL.pop()
    elif discrete:
        pin = list(game.get("_pin") or [1.0] * len(players))
    pts = {i: [] for i in range(len(players))}
    comp = {i: {k: [] for k, _, _, _ in _PROP_SPECS} for i in range(len(players))}
    # The nine numbers DraftKings is paid on, kept only when an auditor asks.
    _CKEYS = ("pass_yd", "pass_td", "int", "rush_yd", "rush_td", "rec", "rec_yd",
              "rec_td", "fum")
    full = ({i: {k: [] for k in _CKEYS} for i in range(len(players))}
            if with_components else None)
    fp_raw = {i: [] for i in range(len(players))}    # for the QB<->WR stack correlation
    # Per-iteration offensive output by team. A defense's score is mostly a
    # function of what the OTHER offense did to it, and nothing here tracked
    # that -- so the two defenses in a game were drawn independently, as if a
    # shootout could punish one and spare the other.
    team_fp = {t: [] for t in teams}
    # Per-iteration DEFENSIVE raw material, collected inside the same iteration
    # the offense was simulated in. A DST used to be a Normal drawn around a
    # Sleeper projection with no connection to the game at all -- the defense
    # facing a 40-point shootout scored exactly like the one pitching a shutout.
    # Now the components DK actually pays for are counted as they happen:
    # offensive touchdowns conceded, interceptions thrown, fumbles lost, and the
    # yardage that drives field goals and sacks.
    team_td = {t: [] for t in teams}       # offensive TDs scored BY this team
    team_gv = {t: [] for t in teams}       # giveaways BY this team (INT + fumbles)
    team_yd = {t: [] for t in teams}       # total offensive yards BY this team
    team_pa = {t: [] for t in teams}       # pass attempts proxy, for sack volume
    gauss = _random.gauss

    for _ in range(n):
        by_team = {t: 0.0 for t in teams}
        tds = {t: 0 for t in teams}
        gvs = {t: 0 for t in teams}
        yds = {t: 0.0 for t in teams}
        patt = {t: 0.0 for t in teams}
        env = max(0.4, gauss(1.0, _ENV_SD))                 # shared game pace/total
        qb_lat = {t: max(0.0, gauss(1.0, _QB_SD)) for t in teams}
        script = {t: gauss(0.0, _SCRIPT_SD) for t in teams}
        if len(teams) >= 2:                                  # scripts are opposite
            script[teams[1]] = -script[teams[0]]
        for i, pl in enumerate(players):
            m, t = pl["means"], pl["team"]
            ql, sc = qb_lat.get(t, 1.0), script.get(t, 0.0)
            pass_f = env * ql * (1 - _SCRIPT_PASS * sc)      # trailing -> more pass
            rush_f = env * (1 + _SCRIPT_RUSH * sc) * max(0.0, gauss(1.0, _RUSH_SD))
            rec_f = env * ql * (1 - _SCRIPT_PASS * sc)       # receivers ride the QB latent
            k = pin[i] if discrete else 1.0          # the upstream mean pin
            pass_yd, rush_yd = m["pass_yd"] * k * pass_f, m["rush_yd"] * k * rush_f
            rec_yd, rec = m["rec_yd"] * k * rec_f, m["rec"] * k * rec_f
            if discrete:
                # a box score is integers: whole yards, whole catches
                pass_yd = float(int(pass_yd + 0.5))
                rush_yd = float(int(rush_yd + 0.5))
                rec_yd = float(int(rec_yd + 0.5))
                rec = float(int(rec + 0.5))
            pass_td = _pois(m["pass_td"] * k * env * ql)
            rush_td = _pois(m["rush_td"] * k * env * (1 + _SCRIPT_RUSH * sc))
            rec_td = _pois(m["rec_td"] * k * env * ql)
            ints = _pois(m["int"])
            fums = _pois(m["fum"])
            fp = _ppr(pass_yd, pass_td, ints, rush_yd, rush_td,
                      rec, rec_yd, rec_td, fums)
            pts[i].append(fp)
            fp_raw[i].append(fp)
            if t in by_team:
                by_team[t] += fp
            if t in tds:
                # A passing TD and its receiving TD are the SAME touchdown, so
                # only the thrower's is counted -- summing both would double the
                # scoreboard. Rushing TDs are counted for whoever ran it in.
                tds[t] += pass_td + rush_td
                gvs[t] += ints + fums
                yds[t] += pass_yd + rush_yd
                patt[t] += pass_yd
            c = comp[i]
            c["pass_yd"].append(pass_yd); c["rush_yd"].append(rush_yd)
            c["rec_yd"].append(rec_yd); c["rec"].append(rec)
            if full is not None:
                fc = full[i]
                fc["pass_yd"].append(pass_yd); fc["pass_td"].append(pass_td)
                fc["int"].append(ints); fc["rush_yd"].append(rush_yd)
                fc["rush_td"].append(rush_td); fc["rec"].append(rec)
                fc["rec_yd"].append(rec_yd); fc["rec_td"].append(rec_td)
                fc["fum"].append(fums)
        for t, v in by_team.items():
            team_fp[t].append(v)
            team_td[t].append(tds[t])
            team_gv[t].append(gvs[t])
            team_yd[t].append(yds[t])
            team_pa[t].append(patt[t])

    def pct(arr, q):
        s = sorted(arr)
        return s[min(len(s) - 1, int(q * len(s)))]

    out, props = [], []
    for i, pl in enumerate(players):
        arr = pts[i]
        raw = sum(arr) / len(arr)
        proj = pl["proj_pts"]
        if discrete:
            # already pinned upstream; multiplying here is exactly what put
            # 47% of the old scores on a grid DraftKings cannot print
            f = 1.0
        else:
            f = proj / raw if raw > 0 else 1.0               # pin points-mean to Sleeper
            arr = [x * f for x in arr]
        boom = proj * 1.5
        row = {"name": pl["name"], "pos": pl["pos"], "team": pl["team"], "opp": pl["opp"],
               "proj_pts": proj, "sim_mean": round(sum(arr) / len(arr), 1),
               # the mean BEFORE any pinning, which the discrete mode's
               # calibration pass reads to size its upstream scale
               "sim_mean_raw": raw,
               "pin_err_pct": (None if not discrete or not proj else
                               round(100.0 * (sum(arr) / len(arr) - proj) / proj, 2)),
               "floor": round(pct(arr, 0.10), 1), "median": round(pct(arr, 0.50), 1),
               "ceiling": round(pct(arr, 0.90), 1),
               "boom_pct": round(100.0 * sum(1 for x in arr if x >= boom) / len(arr), 1),
               "bust_pct": round(100.0 * sum(1 for x in arr if x <= proj * 0.5) / len(arr), 1)}
        if with_samples:
            row["arr"] = [round(x, 2) for x in arr]
        if full is not None:
            # UNSCALED by construction: under discrete the pin is already in
            # the components, and under legacy `arr` was multiplied by
            # proj/raw afterwards, so the stat line recomputes the score only
            # in discrete mode. That asymmetry IS the finding, so the arrays
            # ship as drawn and the auditor measures it.
            row["comps"] = full[i]
            row["arr_raw"] = list(pts[i])
        out.append(row)
        # Component props (correlation is already baked into the samples).
        for key, lab, step, floor_mean in _PROP_SPECS:
            if preseason:
                floor_mean = _PRE_PROP_FLOOR.get(key, floor_mean)
            cs = comp[i][key]
            mean = sum(cs) / len(cs)
            if mean < floor_mean:
                continue
            line = _prop_line(mean, step)
            p_over = sum(1 for x in cs if x > line) / len(cs)
            side, prob = ("More", p_over) if mean >= line else ("Less", 1 - p_over)
            if not (0.52 <= prob <= 0.9):
                continue
            props.append({"player": pl["name"], "pos": pl["pos"], "team": pl["team"],
                          "stat": lab, "line": round(line, 1), "side": side,
                          "prob": round(prob * 100, 1), "proj": round(mean, 1),
                          "matchup": game["label"]})

    out.sort(key=lambda x: -x["ceiling"])
    props.sort(key=lambda x: -x["prob"])
    stacks = _stacks(players, fp_raw, teams)
    return {"label": game["label"], "teams": teams, "players": out,
            "props": props, "stacks": stacks, "n_sims": n,
            # Per-iteration offensive output by team, so a defense can be scored
            # against what it actually faced in that same iteration.
            "team_fp": team_fp if with_samples else None,
            # The DK components each offense conceded, same iteration alignment.
            "team_def": ({t: {"td": team_td[t], "gv": team_gv[t],
                              "yd": team_yd[t], "pa": team_pa[t]} for t in teams}
                         if with_samples else None)}


# League-average rates the DST component model is anchored on, so a simulated
# defense averages roughly what a real one does (~7 DK points) while its SPREAD
# and its ORDERING come from the game it is actually in.
_LG_SACKS = 2.3            # team sacks per game
_LG_PASS_YD = 235.0        # team passing yards per game (sack volume scales on it)
_LG_YD = 335.0             # team total yards per game (FG volume scales on it)
_LG_FG = 1.7               # team field goals per game
_DEF_TD_P = 0.075          # P(a defensive TD) per takeaway forced
# DK pays the DST for punt/kick/FG RETURN touchdowns too, and those do not need
# a turnover to happen. Leaving them out is what made the simulated ceiling far
# too thin: a defense's boom week is a return score, and without one the model
# put 15+ point games at ~4% against a real rate near 11%. A DST is a
# threshold-and-tail asset -- if the tail is missing, GPP never rosters one for
# the right reason.
_ST_TD_P = 0.035           # P(a special-teams return TD) per game
_SAFETY_P = 0.007          # P(a safety) per game


def _dst_from_components(opp_td, opp_gv, opp_yd, opp_pa, rng):
    """DK points for a defense, per iteration, from what the opposing offense
    actually did in THAT iteration.

    The old regular-season model drew a defense from Normal(projection, 0.7*proj
    + 4) -- an independent bell curve. Nothing tied it to the game, so the DST
    opposite a 40-burger scored like the one pitching a shutout, and a lineup
    that paired a defense with the shootout it was in looked perfectly fine to
    the optimizer. Preseason already scored defenses against the offense they
    faced; the regular season, which is the one people play, did not.

    Every term below is a DraftKings category (dk_scoring.NFL_DST): points
    allowed off the touchdowns and field goals conceded in this very iteration,
    sacks scaled by how much the opponent threw, takeaways straight from the
    interceptions and fumbles the offensive sim already sampled, and the
    occasional return touchdown or safety."""
    s, out = dk_scoring.NFL_DST, []
    n = len(opp_td)
    for i in range(n):
        # Points allowed: touchdowns are worth 7 with the extra point, and field
        # goals scale with how much yardage the offense actually moved.
        fg = _pois(_LG_FG * max(0.25, opp_yd[i] / _LG_YD), rng)
        pa = 7 * opp_td[i] + 3 * fg
        pts = dk_scoring.nfl_dst_pa_points(pa)
        # Sacks: more dropbacks, more chances.
        pts += s["sack"] * _pois(_LG_SACKS * max(0.3, opp_pa[i] / _LG_PASS_YD), rng)
        # Takeaways are NOT re-rolled -- they are the same interceptions and
        # fumbles the offense lost in this iteration, so the two sides of the
        # ball agree with each other.
        gv = opp_gv[i]
        pts += s["int"] * gv                      # int and fumble_rec both pay 2
        if gv and rng.random() < _DEF_TD_P * gv:
            pts += s["int_td"]                    # returned a takeaway
        if rng.random() < _ST_TD_P:
            pts += s["return_td"]                 # punt / kick / FG return
        if rng.random() < _SAFETY_P:
            pts += s["safety"]
        out.append(round(pts, 2))
    return out


def _corr(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / den if den else 0.0


def _stacks(players, fp_raw, teams):
    """QB + his two best pass-catchers per team, with the sim's combined ceiling and
    the QB<->receiver correlation that makes the stack a best-ball weapon."""
    out = []
    for t in teams:
        qb_i = next((i for i, p in enumerate(players) if p["team"] == t and p["pos"] == "QB"), None)
        if qb_i is None:
            continue
        recs = sorted([i for i, p in enumerate(players)
                       if p["team"] == t and p["pos"] in ("WR", "TE")],
                      key=lambda i: -players[i]["proj_pts"])[:2]
        if not recs:
            continue
        combo = [fp_raw[qb_i][s] + sum(fp_raw[r][s] for r in recs) for s in range(len(fp_raw[qb_i]))]
        combo.sort()
        ceil = combo[min(len(combo) - 1, int(0.90 * len(combo)))]
        corr = sum(_corr(fp_raw[qb_i], fp_raw[r]) for r in recs) / len(recs)
        out.append({"team": t, "qb": players[qb_i]["name"],
                    "receivers": [players[r]["name"] for r in recs],
                    "combined_ceiling": round(ceil, 1),
                    "qb_wr_corr": round(corr, 2)})
    return out


# ---- DST projections (Sleeper) + full DFS player pool ----------------------
def dst_projections(season, week):
    """{team_abbr: {nickname, proj}} for team defenses. Cached 1h."""
    def build():
        url = (f"{_PROJ.format(season=season, week=week)}?season_type=regular"
               f"&position[]=DEF&order_by=pts_ppr")
        try:
            rows = _get(url)
        except Exception:
            return None
        out = {}
        for r in rows:
            pts = (r.get("stats") or {}).get("pts_ppr")
            if pts is None:
                continue
            p = r.get("player") or {}
            out[r.get("team")] = {"nickname": p.get("last_name") or r.get("team"),
                                  "proj": round(pts, 2)}
        return out or None
    return _cached(("nfl_dst", season, week), 3600, build)


def kicker_projections(season, week):
    """{team_abbr: {name, fg: [(dk_pts, made_per_game), ...], xpm, xpa, pts}}
    from Sleeper's kicker rows. Cached 1h.

    Sleeper's `fgm` runs ahead of its own distance buckets (Myers, 2026 week
    1: fgm 1.97 against buckets summing to 1.30) and its point total is built
    from the BUCKETS, so the buckets are what is trusted here -- a made kick
    with no distance is not worth inventing a distance for."""
    def build():
        url = (f"{_PROJ.format(season=season, week=week)}?season_type=regular"
               f"&position[]=K&order_by=pts_std")
        try:
            rows = _get(url)
        except Exception as _e:
            errlog.note("NFLDFS-sleeper-k", _e)
            return None
        out = {}
        for r in rows:
            st = r.get("stats") or {}
            p = r.get("player") or {}
            if (p.get("position") or "") != "K" or not r.get("team"):
                continue
            fg = [(dk_scoring.NFL_K["fg_0_39"],
                   sum(float(st.get(k) or 0.0) for k in ("fgm_0_19", "fgm_20_29", "fgm_30_39"))),
                  (dk_scoring.NFL_K["fg_40_49"], float(st.get("fgm_40_49") or 0.0)),
                  (dk_scoring.NFL_K["fg_50p"], float(st.get("fgm_50p") or 0.0))]
            xpm = float(st.get("xpm") or 0.0)
            xpa = float(st.get("xpa") or 0.0) or xpm
            pts = sum(v * m for v, m in fg) + dk_scoring.NFL_K["xp"] * xpm
            if pts <= 0:
                continue
            out[r.get("team")] = {
                "name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
                "fg": fg, "xpm": xpm, "xpa": xpa, "pts": round(pts, 2)}
        return out or None
    return _cached(("nfl_k", season, week), 3600, build)


def _kicker_arr(k, off, n, rng, discrete=False):
    """A kicker's per-iteration DK points off HIS OWN offense's simulated
    output: an extra point per offensive touchdown that iteration (made at
    his xpm/xpa), field goals as a Poisson whose mean rides the offense's
    yardage that iteration (more drives that move, more kicks), each made
    kick's distance drawn from the projection's own mix. Mean pinned to the
    projection like every other player; the shape and the correlation with
    his quarterback are the point -- a kicker in a shootout scores, one in
    a shutout does not, and the old pool had no kicker at all.

    A kicker's DK categories are all whole numbers (1 for the extra point, 3
    / 4 / 5 for a field goal by distance), so `pts` below comes out of the
    loop already legal -- and then the legacy path multiplies it by
    projection/raw and throws that away. How much of it lands somewhere
    DraftKings cannot print depends on whether that one factor happens to be a
    multiple of 0.02, so it is arbitrary rather than bounded: 47% of a
    kicker's scores on the week-1 CAR @ CHI pool, 95% on the week-2 DEN @ KC
    one (research/sd_support).

    discrete=True pins the level UPSTREAM instead, on the only lever that is
    a real rate: the field-goal Poisson mean. Extra points are not scalable
    here, because they are one per touchdown the offense actually scored in
    that same world and moving them would break the tie to the game. Two
    solve passes (the kick count is linear in its own mean, so one Newton
    step converges), and whatever is left is REPORTED rather than multiplied
    away -- a multiply is the defect, at any size."""
    tds, yds = off.get("td") or [], off.get("yd") or []
    if len(tds) < n or len(yds) < n:
        return None
    fg_mean = sum(m for _v, m in k["fg"])
    mix = [(v, m) for v, m in k["fg"] if m > 0]
    tot_m = sum(m for _v, m in mix) or 1.0
    xp_rate = (k["xpm"] / k["xpa"]) if k["xpa"] else 0.94
    mean_yd = (sum(yds) / len(yds)) or 1.0

    def draw(scale=1.0):
        out, xps = [], 0.0
        for i in range(n):
            xp = sum(1 for _ in range(int(tds[i])) if rng.random() < xp_rate)
            f = min(2.5, max(0.3, yds[i] / mean_yd))
            made = _pois(fg_mean * scale * f, rng)
            pts = dk_scoring.NFL_K["xp"] * xp
            for _ in range(made):
                u = rng.random() * tot_m
                for v, m in mix:
                    u -= m
                    if u <= 0:
                        pts += v
                        break
                else:
                    pts += mix[-1][0]
            out.append(float(pts))
            xps += dk_scoring.NFL_K["xp"] * xp
        return out, xps / n

    out, xp_mean = draw()
    if not discrete:
        raw = sum(out) / n
        f = k["pts"] / raw if raw > 0 else 1.0
        return [round(x * f, 2) for x in out]
    want, scale = float(k["pts"] or 0.0), 1.0
    for _ in range(2):
        raw = sum(out) / n
        fg_part = raw - xp_mean           # what the kicks, not the XPs, are worth
        if want <= xp_mean:
            # The extra points alone already clear the projection, and an extra
            # point is one per touchdown his offense scored in that world -- not
            # ours to scale without cutting the tie to the game. Kick as little
            # as the rate allows and report the overshoot; do not multiply.
            out, xp_mean = draw(0.0)
            break
        if fg_part <= 1e-9:
            break
        scale *= (want - xp_mean) / fg_part
        out, xp_mean = draw(max(0.0, scale))
        if abs(sum(out) / n - want) <= 0.01 * max(want, 1e-9):
            break
    return [round(x, 2) for x in out]


MODELS = ("legacy", "constrained")


def player_pool(week, n=3000, preseason=False, season=None, teams=None, model="legacy", seed=None, xside=0.0, discrete=False):
    """Every DFS-relevant player for a week: skill players carry correlated point
    arrays from the game sims; DSTs carry independent Normal-sampled arrays from
    Sleeper's team-defense projection. {name: {pos, team, proj, ceiling, floor, arr}}.
    Cached 30m (this is the heavy correlated sim over the whole slate).

    `teams` narrows the pool to the games those clubs play in -- a showdown
    is one game, and simulating sixteen at 3,000 draws to build it was both
    the wrong cost and the wrong depth: the one game gets simulated deep
    instead (nfl_dfs._SD_SIMS), on its own cache key.

    `model` picks the game simulator: "legacy" (this module's latent model,
    the production default, unseeded) or "constrained" (nfl_dfs_csim, the
    alternate mode under validation: the reconciled line, the team's books
    balanced in every world, child-seeded per game from `seed` so a board is
    reproducible draw for draw; the defenses and kickers ride the same
    seed). Both run the same defense and kicker models off the offense the
    model produced. `proj` stays Sleeper's number under either model: the
    field is built on what the public reads."""
    season = season or _season()      # a backtest names a past season
    want = frozenset(str(t).upper() for t in (teams or ()) if t)
    if model not in MODELS:
        raise ValueError(f"unknown simulator model {model!r}")
    if model == "constrained" and preseason:
        raise ValueError("the constrained model has no preseason projections to reconcile")

    def build():
        games = (preseason_games(str(season), week) if preseason
                 else weekly_games(str(season), week))
        if games and want:
            games = {gid: g for gid, g in games.items()
                     if want & ({str(t).upper() for t in (g.get("teams") or [])}
                                | {str(pl.get("team")).upper() for pl in (g.get("players") or [])})}
        if not games:
            return None
        pool = {}
        pre_dst = {}
        sim_dst = {}
        team_off = {}
        team_rng = {}
        side_rng = _random                  # defenses and kickers: the module generator (legacy)
        if model == "constrained":
            import random as _rnd
            import nfl_dfs_csim
            from research import seeds as _seeds
            _np = nfl_dfs_csim.np            # numpy lives behind nfl_dfs_csim's ImportError guard
            if _np is None:
                raise RuntimeError("numpy is required for the constrained model")
        for gid, g in games.items():
            if model == "constrained":
                child = _seeds.child_seed(seed, season, week, gid, "constrained") if seed is not None else None
                sim = nfl_dfs_csim.simulate_game(g, n=n, rng=_np.random.default_rng(child), xside=xside)
                side_rng = _rnd.Random(child) if seed is not None else _random
                for p in sim["players"]:
                    p["arr"] = _np.round(p["arr"], 2).tolist()
                sim["team_def"] = {t: {k: v.tolist() for k, v in d.items()} for t, d in sim["team_def"].items()}
            else:
                sim = simulate_game(g, n=n, with_samples=True, preseason=preseason, discrete=discrete)
            for t, d in (sim.get("team_def") or {}).items():
                team_off[t] = d                 # this offense's own per-iteration output
                team_rng[t] = side_rng
            # projected targets ride along: the receiving-back stack rule
            # (dfs_tourney.RB_STACK_TARGETS) needs them, and nothing else in
            # the pool carries a component
            tgt_of = {pl["name"]: float((pl.get("recon") or pl.get("means") or {}).get("rec_tgt") or 0.0)
                      for pl in (g.get("players") or [])}
            for p in sim["players"]:
                pool[p["name"]] = {"pos": p["pos"], "team": p["team"], "opp": p.get("opp"),
                                   "proj": p["proj_pts"], "ceiling": p["ceiling"],
                                   "floor": p["floor"], "arr": p["arr"], "sim_mean": p.get("sim_mean"),
                                   "rec_tgt": tgt_of.get(p["name"], 0.0)}
            # In August a defense is scored against the offense it faced in that
            # same iteration, so the two defenses in a game move together and a
            # shootout punishes both. Sleeper's regular-season DST projection --
            # which dst_projections asks for unconditionally -- knows nothing
            # about either.
            tfp = sim.get("team_fp") or {}
            tdef = sim.get("team_def") or {}
            if preseason and len(tfp) >= 2:
                import nfl_preseason as _np
                ts = list(tfp)
                for me, opp in ((ts[0], ts[1]), (ts[1], ts[0])):
                    arr = _np.dst_from_offense(tfp[opp], n, _random)
                    if arr:
                        pre_dst[me] = arr
            elif len(tdef) >= 2:
                # Regular season: score each defense off the DK categories the
                # OTHER offense produced in the same iteration.
                ts = list(tdef)
                for me, opp in ((ts[0], ts[1]), (ts[1], ts[0])):
                    d = tdef[opp]
                    arr = _dst_from_components(d["td"], d["gv"], d["yd"], d["pa"], side_rng)
                    if arr:
                        sim_dst[me] = arr
        dst = dst_projections(str(season), week) or {}
        for team, d in dst.items():
            arr = pre_dst.get(team)
            if arr:
                proj = round(sum(arr) / len(arr), 2)
            elif sim_dst.get(team):
                # Shape from the game, LEVEL from Sleeper: the component model
                # knows this defense is in a shootout, but it does not know the
                # unit's own quality (it carries no personnel). Shifting to
                # Sleeper's mean keeps the good defenses good while the spread
                # and the game-by-game ordering stay the simulation's. A shift,
                # not a rescale -- multiplying would distort the points-allowed
                # tiers that give a DST its lumpy, threshold-shaped upside.
                arr = sim_dst[team]
                raw = sum(arr) / len(arr)
                shift = d["proj"] - raw
                # -4 is the WORST score DK's rules can produce (the 35+ points
                # allowed tier; every other category only adds). A negative
                # shift must not manufacture scores below it.
                if discrete:
                    # Every DK defensive category is a whole number and so is
                    # every points-allowed tier, so `arr` arrives integral --
                    # and a shift of, say, +1.37 then moves ALL of it off the
                    # lattice. How much lands somewhere DraftKings cannot print
                    # is decided by one accident -- whether the shift happens to
                    # be a multiple of 0.02 -- so it ran 44% on one measured
                    # board and 100% on the next (research/sd_support).
                    #
                    # An integer distribution cannot be moved by a fractional
                    # amount and stay integral, so the fraction is spent as a
                    # coin: floor(shift) always, one more point with
                    # probability frac(shift). The mean lands exactly where
                    # the plain shift put it, the support stays legal, and the
                    # cost is one Bernoulli's variance -- at most 0.25 points
                    # squared against a defense's ~60, under half a percent.
                    # This is the only term in the whole model whose level
                    # comes from outside the game (Sleeper knows the unit's
                    # quality and the component model carries no personnel),
                    # so it is the only one that needs the coin.
                    base = _math.floor(shift)
                    frac = shift - base
                    _rg = team_rng.get(team, _random)   # the generator this defense was drawn on
                    arr = [round(max(-4.0, x + base
                                     + (1 if _rg.random() < frac else 0)), 2)
                           for x in arr]
                else:
                    arr = [round(max(-4.0, x + shift), 2) for x in arr]
                proj = d["proj"]
            else:
                proj = d["proj"]
                sd = 0.7 * proj + 4.0                    # DST scoring is high-variance
                arr = [round(max(-4.0, _random.gauss(proj, sd)), 2) for _ in range(n)]
            for key in {d["nickname"], team}:            # match DK by nickname or abbr
                pool[key] = {"pos": "DST", "team": team, "opp": None, "proj": proj,
                             "ceiling": round(sorted(arr)[int(0.9 * len(arr))], 1),
                             "floor": round(sorted(arr)[int(0.1 * len(arr))], 1), "arr": arr}
        # Kickers, regular season: DK's Showdown pool carries them and they are
        # a standard play there (Myers averaged 12 DK points in 2025 at $5,400
        # tonight); the pool had none, so every showdown build was choosing
        # from a board with the kicker slot cut out. Scored off the offense's
        # own iterations (_kicker_arr), so a kicker and his quarterback move
        # together.
        if not preseason:
            for team, k in (kicker_projections(str(season), week) or {}).items():
                off = team_off.get(team)
                k_rng = team_rng.get(team, _random)
                if not off or k["name"] in pool:
                    continue
                arr = _kicker_arr(k, off, n, k_rng, discrete=discrete)
                if not arr:
                    continue
                pool[k["name"]] = {"pos": "K", "team": team, "opp": None,
                                   "proj": k["pts"],
                                   "ceiling": round(sorted(arr)[int(0.9 * len(arr))], 1),
                                   "floor": round(sorted(arr)[int(0.1 * len(arr))], 1),
                                   "arr": arr}
        # A preseason defense whose team Sleeper did not list still gets its
        # simulated array rather than dropping out of the pool entirely.
        for team, arr in pre_dst.items():
            if team in pool:
                continue
            pool[team] = {"pos": "DST", "team": team, "opp": None,
                          "proj": round(sum(arr) / len(arr), 2),
                          "ceiling": round(sorted(arr)[int(0.9 * len(arr))], 1),
                          "floor": round(sorted(arr)[int(0.1 * len(arr))], 1), "arr": arr}
        return pool or None
    return _cached(("nfl_pool", season, week, n, bool(preseason), tuple(sorted(want)) or None, model, seed, float(xside or 0.0), bool(discrete)),
                   1800, build)


# ---- Week board (all games simmed) -----------------------------------------
def _season():
    t = clock.today_et()
    return t.year if t.month >= 3 else t.year - 1


_board_inflight = set()


_BOARD_TTL = 1800


def board(week=1, preseason=False):
    """Non-blocking week sim board: cached if fresh, else kick a background build
    (Sleeper fetch + 16 correlated game sims) and return None while it runs.

    Shared across workers via boardshare (one build serves every worker), and
    the two failure shapes are cached briefly instead of dropped: a None build
    used to cache NOTHING, so every poll spawned another build forever, and an
    exception in the thread had no handler at all -- the board just stayed
    "simulating..." with the reason lost."""
    import boardshare
    season = _season()
    key = ("nfl_sim_board", season, week, bool(preseason))
    name = f"nfl_sim_board_{season}_w{week}_{int(bool(preseason))}"
    hit = _cache.get(key)
    if hit and _time.time() - hit[0] < _BOARD_TTL:
        return hit[1]
    disk, age = boardshare.get(name, _BOARD_TTL)
    if disk is not None:                     # a sibling already built it
        _cache[key] = (_time.time() - age, disk)
        return disk
    if key not in _board_inflight and boardshare.claim(name):
        _board_inflight.add(key)

        def _bg():
            try:
                val = _build_board(season, week, preseason=preseason)
                if val is None:
                    val = {"season": season, "week": week,
                           "preseason": bool(preseason), "games": [],
                           "empty": True,
                           "note": ("No games found for this week. Preseason "
                                    "runs weeks 1-4 in August; the regular "
                                    "season starts in September.")}
                    _cache[key] = (_time.time() - 1500, val)   # retry in ~5m
                    boardshare.put(name, val, age=1500)
                else:
                    _cache[key] = (_time.time(), val)
                    boardshare.put(name, val)
            except Exception as e:
                errlog.note("NFLDFS-board-build", e,
                            path=f"s{season} w{week} pre={int(bool(preseason))}")
                val = {"season": season, "week": week,
                       "preseason": bool(preseason), "games": [], "empty": True,
                       "error": str(e),
                       "note": "The sim board could not be built; retrying shortly."}
                _cache[key] = (_time.time() - 1680, val)
                boardshare.put(name, val, age=1680)
            finally:
                _board_inflight.discard(key)
                boardshare.release(name)
        _threading.Thread(target=_bg, daemon=True).start()
    return hit[1] if hit else None


def _build_board(season, week, n=4000, preseason=False):
    games = (preseason_games(str(season), week) if preseason
             else weekly_games(str(season), week))
    if not games:
        return None
    # Sleeper consensus draft rank (~ADP) for best-ball value, if available.
    adp = {}
    try:
        import nfl_adp
        for nm, v in (nfl_adp.consensus() or {}).items():
            if v.get("rank"):
                adp[nm] = v["rank"]                 # keyed by lowercased name
    except Exception:
        adp = {}

    sims, ceilings, props, stacks = [], [], [], []
    for gid, g in games.items():
        s = simulate_game(g, n=n, preseason=preseason)
        sims.append(s)
        props.extend(s["props"])
        for st in s["stacks"]:
            st["matchup"] = s["label"]
            stacks.append(st)
        for p in s["players"]:
            p2 = dict(p, matchup=s["label"], adp=adp.get(p["name"].lower()))
            ceilings.append(p2)
    if not sims:
        return None
    sims.sort(key=lambda x: -max((p["ceiling"] for p in x["players"]), default=0))
    ceilings.sort(key=lambda x: -x["ceiling"])
    # Ranking props by raw probability just surfaces the lowest-variance stat
    # (receptions unders) over and over. Instead round-robin across the stat types
    # -- each already sorted by confidence -- so the board stays varied, capped at
    # two props per player.
    buckets = {}
    for p in sorted(props, key=lambda x: -x["prob"]):
        buckets.setdefault(p["stat"], []).append(p)
    order = ["pass yds", "rush yds", "rec yds", "receptions"]
    props, seen = [], {}
    while any(buckets.get(s) for s in order):
        for s in order:
            b = buckets.get(s)
            if not b:
                continue
            p = b.pop(0)
            if seen.get(p["player"], 0) >= 2:
                continue
            seen[p["player"]] = seen.get(p["player"], 0) + 1
            props.append(p)
    stacks.sort(key=lambda x: -x["combined_ceiling"])
    note = ("Correlated per-game Monte Carlo seeded by Sleeper's weekly "
            "projections. Player means are pinned to Sleeper; the sim adds "
            "floor/ceiling shape and same-game correlation (QB<->WR stacks).")
    if preseason:
        note = ("PRESEASON lineups. Sleeper projects nothing for exhibitions, so "
                "the means are measured instead: one team-game from 96 of last "
                "August's, distributed by a usage model that runs INVERTED -- the "
                "backup quarterback throws about a third more than the starter, a "
                "camp-body running back gets roughly double a starter's touches, "
                "and the starters sit after a series. Where Kalshi books a player "
                "his own ladder sets his level. Chalk is upside down here: the "
                "names at the top of a DraftKings salary list are the ones who "
                "will not play.")
    return {"season": season, "week": week, "n_games": len(sims), "n_sims": n,
            "games": sims, "ceilings": ceilings[:80], "props": props[:80],
            "stacks": stacks[:16], "has_adp": bool(adp),
            "preseason": bool(preseason), "note": note}
