"""Madden-style NFL season simulator for fantasy / DFS.

Like Madden's franchise sim (and the MLB deep engine), this doesn't run literal
play-by-play — it drives off each player's production 'ratings' (here: their
recency-weighted multi-year stat line) and Monte-Carlos a season game by game.
Per game it samples yardage with realistic spread and touchdowns as lumpy
(Poisson) events — which is what gives fantasy its boom/bust shape — then scores
it in DraftKings points. Out of many simulated seasons we get each player's
per-game fantasy distribution (proj / floor / ceiling) and full-season total: the
projection layer DFS lineups are built on, and a season-long value board for
best-ball drafts in the meantime.
"""

import math
import random

import nfl_awards

GAMES = 17

# DraftKings NFL scoring.
def _dk(pass_yds, pass_td, ints, rush_yds, rush_td, rec, rec_yds, rec_td):
    pts = (0.04 * pass_yds + 4 * pass_td - 1 * ints
           + 0.1 * rush_yds + 6 * rush_td
           + 0.1 * rec_yds + 6 * rec_td + 1.0 * rec)            # full PPR
    if pass_yds >= 300:
        pts += 3
    if rush_yds >= 100:
        pts += 3
    if rec_yds >= 100:
        pts += 3
    return pts


def _pois(lam, rng):
    if lam <= 0:
        return 0
    L = math.exp(-min(30, lam)); k = 0; p = 1.0
    while True:
        k += 1; p *= rng.random()
        if p <= L:
            return k - 1


def _game(pg, rng):
    """One game's DK points for a player from per-game expected components.
    Yardage is sampled with position-typical spread; TDs are lumpy Poisson events
    (the source of fantasy ceilings)."""
    def yd(mean, cv):
        return max(0.0, rng.gauss(mean, mean * cv)) if mean > 0 else 0.0
    pass_yds = yd(pg["pass_yds"], 0.28)
    rush_yds = yd(pg["rush_yds"], 0.55)
    rec_yds = yd(pg["rec_yds"], 0.60)
    rec = max(0, round(rng.gauss(pg["rec"], pg["rec"] * 0.45))) if pg["rec"] > 0 else 0
    return _dk(pass_yds, _pois(pg["pass_td"], rng), _pois(pg["int"], rng),
               rush_yds, _pois(pg["rush_td"], rng),
               rec, rec_yds, _pois(pg["rec_td"], rng))


_FANTASY_POS = {"QB", "RB", "WR", "TE"}
_DRAFT = "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl"

# Expected rookie fantasy points/game by draft slot (peak * exp(-pick/scale) +
# floor), calibrated to how rookie production actually breaks down by where a
# player is taken. Draft capital is the single best predictor of rookie touches.
_ROOK = {"RB": (13.0, 45, 1.5), "WR": (11.0, 55, 1.5),
         "TE": (8.0, 35, 0.8), "QB": (15.0, 22, 1.0)}


def _rookie_fppg(pos, pick):
    peak, scale, floor = _ROOK[pos]
    val = peak * math.exp(-pick / scale) + floor
    if pos == "QB" and pick > 64:        # late QBs almost never start as rookies
        val = min(val, 3.0)
    return val


def rookies(season):
    """Skill-position rookies from this year's draft as
    [{name, pos, team_abbr, pick, base_fppg}]. Cached a month (the draft is set)."""
    def build():
        import pro_data
        teams = {t["id"]: t for t in pro_data.teams("nfl")}
        try:
            rd = nfl_awards._get(f"{_DRAFT}/seasons/{season}/draft/rounds?lang=en")
        except Exception:
            return []
        picks = []
        for r in rd.get("items", [])[:5]:            # rounds 1-5 cover fantasy rookies
            pk = r.get("picks")
            if isinstance(pk, dict):
                try:
                    pk = nfl_awards._get(pk["$ref"]).get("items", [])
                except Exception:
                    pk = []
            picks += pk or []

        def resolve(pk):
            a_ref = (pk.get("athlete") or {}).get("$ref")
            if not a_ref:
                return None
            try:
                a = nfl_awards._get(a_ref)
            except Exception:
                return None
            pos = (a.get("position") or {}).get("abbreviation", "")
            if pos not in _FANTASY_POS:
                return None
            tref = ((pk.get("team") or {}).get("$ref") or "")
            tid = tref.split("/teams/")[-1].split("?")[0] if tref else None
            overall = pk.get("overall") or 260
            return {"name": a.get("displayName"), "pos": pos,
                    "team_abbr": (teams.get(tid) or {}).get("abbrev"),
                    "pick": overall, "base_fppg": _rookie_fppg(pos, overall)}
        import concurrent.futures as cf
        out = []
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            for r in ex.map(resolve, picks):
                if r and r["name"]:
                    out.append(r)
        return out
    return nfl_awards.racing._cached(("nfl_rookies", season), 30 * 86400, build) or []


def project(prior_season=None, n_seasons=4000, seed=None):
    """Per-player fantasy projection board from the season Monte Carlo."""
    import datetime
    prior_season = prior_season or datetime.date.today().year - 1
    cands = nfl_awards.candidates(prior_season)
    if not cands:
        return None
    rng = random.Random(seed)
    rows = []
    for pid, p in cands.items():
        if p["pos"] not in _FANTASY_POS:
            continue
        # multi-year production -> regressed projection, then per-game expectation
        proj = nfl_awards._proj(p["stats"])
        pg = {k: proj.get(k, 0) / GAMES for k in
              ("pass_yds", "pass_td", "int", "rush_yds", "rush_td", "rec_yds", "rec_td", "rec")}
        pg["int"] = proj.get("pass_yds", 0) / GAMES * 0.0007 if proj.get("pass_yds") else 0  # ~ INT rate
        per_game, totals = [], []
        for _ in range(n_seasons):
            games = int(round(GAMES * max(0.3, min(1.0, rng.gauss(0.92, 0.13)))))  # injury
            form = math.exp(rng.gauss(0.0, 0.12))                                  # season form
            season = 0.0
            for _g in range(games):
                fp = _game({k: v * form for k, v in pg.items()}, rng)
                per_game.append(fp)
                season += fp
            totals.append(season)
        per_game.sort(); totals.sort()
        n = len(per_game)
        q = lambda a, f: a[min(len(a) - 1, int(f * len(a)))]
        rows.append({
            "id": pid, "name": p["name"], "pos": p["pos"], "team": p["team_abbr"],
            "fppg": round(sum(per_game) / n, 1),
            "floor": round(q(per_game, 0.25), 1), "ceiling": round(q(per_game, 0.85), 1),
            "boom": round(q(per_game, 0.97), 1),               # smash-game upside (GPP)
            "season": round(sum(totals) / len(totals), 1), "rookie": False,
        })

    # Rookies: no NFL stats, so projected from draft capital + landing spot, with
    # high variance (boom/bust) — exactly the best-ball dart-throw profile.
    tw = nfl_awards._team_wins()
    for rk in rookies(prior_season + 1):
        if not rk["team_abbr"]:
            continue
        tf = max(0.6, 0.85 + 0.04 * (tw.get(rk["team_abbr"], 8.5) - 8))
        mean = rk["base_fppg"] * tf
        per_game, totals = [], []
        for _ in range(n_seasons):
            games = int(round(GAMES * max(0.25, min(1.0, rng.gauss(0.85, 0.18)))))
            season = 0.0
            for _g in range(games):
                fp = max(0.0, rng.gauss(mean, mean * 0.62))    # rookie boom/bust
                per_game.append(fp); season += fp
            totals.append(season)
        per_game.sort(); totals.sort()
        q = lambda a, f: a[min(len(a) - 1, int(f * len(a)))]
        rows.append({
            "id": "rk_" + (rk["name"] or ""), "name": rk["name"], "pos": rk["pos"],
            "team": rk["team_abbr"], "fppg": round(sum(per_game) / len(per_game), 1),
            "floor": round(q(per_game, 0.25), 1), "ceiling": round(q(per_game, 0.85), 1),
            "boom": round(q(per_game, 0.97), 1),
            "season": round(sum(totals) / len(totals), 1),
            "rookie": True, "pick": rk["pick"],
        })
    # Pull in draftable consensus players the production pool is missing (expected
    # starters with little past production -- injury returners, promoted backups),
    # projected at the role their consensus rank implies. Done BEFORE VOR so they
    # are valued on the same scale as everyone else.
    try:
        import nfl_adp
        nfl_adp.inject(rows)
    except Exception:
        pass

    # Draft value = value over replacement (VOR): season projection minus the
    # last startable player at that position in a 12-team best-ball league. This
    # is what makes positions comparable on a draft board.
    _REPL = {"QB": 16, "RB": 36, "WR": 48, "TE": 16}
    for pos in _FANTASY_POS:
        lst = sorted((r for r in rows if r["pos"] == pos), key=lambda x: -x["season"])
        repl = lst[min(len(lst) - 1, _REPL[pos])]["season"] if lst else 0.0
        for r in lst:
            r["value"] = round(r["season"] - repl, 1)
    # Blend value toward the live draft consensus and flag FA / new-team / injury
    # returners, so players whose PAST stats understate their EXPECTED 2026 role
    # (Deebo, Charbonnet, Tank Dell...) are no longer buried. Self-corrects as the
    # consensus + injury feeds update through the offseason.
    try:
        import nfl_adp
        nfl_adp.blend(rows)
    except Exception:
        pass
    rows.sort(key=lambda r: -r["value"])
    for i, r in enumerate(rows, 1):
        r["adp"] = i                                   # consensus-blended value rank
    by_pos = {}
    for pos in _FANTASY_POS:
        by_pos[pos] = [r for r in rows if r["pos"] == pos][:30]
    return {"sport": "nfl_dfs", "season": prior_season + 1, "n_seasons": n_seasons,
            "pool": rows, "overall": rows[:40], "by_pos": by_pos}


# ---- Best-ball team grader --------------------------------------------------
# DraftKings best-ball starting lineup: 1 QB, 2 RB, 3 WR, 1 TE, 1 FLEX (RB/WR/TE).
_START = {"QB": 1, "RB": 2, "WR": 3, "TE": 1}
_FLEX = ("RB", "WR", "TE")
# Healthy roster construction for an 18-pick best-ball team (need bodies to fill
# the lineup every week across byes / bad games).
_DEPTH_TARGET = {"QB": 2, "RB": 5, "WR": 6, "TE": 2}


def _optimal(roster, key):
    """Best lineup by `key` (fppg or boom) + its total. Best-ball auto-starts your
    top players, so the team's weekly output is this optimum, not a fixed lineup."""
    by = {"QB": [], "RB": [], "WR": [], "TE": []}
    for p in roster:
        if p.get("pos") in by:
            by[p["pos"]].append(p)
    for v in by.values():
        v.sort(key=lambda p: -(p.get(key) or 0))
    starters, used = [], set()
    for pos, n in _START.items():
        for p in by[pos][:n]:
            starters.append(p); used.add(id(p))
    flex_pool = sorted((p for p in roster if p.get("pos") in _FLEX and id(p) not in used),
                       key=lambda p: -(p.get(key) or 0))
    if flex_pool:
        starters.append(flex_pool[0])
    return starters, sum((p.get(key) or 0) for p in starters)


def _letter(score):
    for cut, g in ((90, "A+"), (83, "A"), (78, "A-"), (72, "B+"), (66, "B"),
                   (60, "B-"), (54, "C+"), (48, "C"), (42, "C-"), (35, "D+"),
                   (28, "D"), (0, "F")):
        if score >= cut:
            return g
    return "F"


def _par_teams(pool, teams=12, rounds=18):
    """Reference 'chalk' teams: draft straight down ADP from each draft slot
    (snake), giving the field of typical drafted rosters to grade against."""
    ranked = sorted(pool, key=lambda p: p.get("adp", 9999))
    out = []
    for slot in range(1, teams + 1):
        picks, overalls = [], []
        for r in range(rounds):
            o = r * teams + (slot if r % 2 == 0 else teams - slot + 1)
            overalls.append(o - 1)               # 0-indexed ADP rank
        for idx in overalls:
            if idx < len(ranked):
                picks.append(ranked[idx])
        if len(picks) >= 8:
            out.append(picks)
    return out


def _team_score(roster):
    """Best-ball value: weekly projection plus a heavy weight on ceiling (boom
    weeks win best ball)."""
    _, proj = _optimal(roster, "fppg")
    _, ceil = _optimal(roster, "boom")
    return 0.6 * proj + 0.4 * ceil, proj, ceil


def grade_roster(roster, pool=None):
    """Grade a best-ball roster: overall letter + 0-100, per-position strength, the
    optimal lineup, stacks, and plain-English strengths/weaknesses."""
    roster = [p for p in roster if p and p.get("pos") in _FANTASY_POS]
    if len(roster) < 4:
        return {"error": "Add at least a few players to grade a team."}
    if pool is None:
        b = board()
        pool = (b or {}).get("pool") or []

    score_raw, proj, ceil = _team_score(roster)
    starters, _ = _optimal(roster, "fppg")
    start_ids = {id(p) for p in starters}

    # Grade vs the field of chalk ADP teams (ratio to the average par team).
    par = _par_teams(pool) if pool else []
    par_scores = [_team_score(t)[0] for t in par]
    par_mean = (sum(par_scores) / len(par_scores)) if par_scores else score_raw or 1.0
    ratio = score_raw / par_mean if par_mean else 1.0
    score = max(0, min(100, 50 + (ratio - 1) * 300))

    # Positional strength: average positional rank (in the pool) of the players
    # you'd actually start at each spot.
    pool_rank = {}
    for pos in _FANTASY_POS:
        lst = sorted((p for p in pool if p.get("pos") == pos), key=lambda x: -(x.get("season") or 0))
        for i, p in enumerate(lst, 1):
            pool_rank[(pos, p.get("name"))] = i
    counts = {pos: sum(1 for p in roster if p.get("pos") == pos) for pos in _FANTASY_POS}
    positions, strengths, weaknesses = {}, [], []
    # par counts at each position (how deep the average team goes)
    for pos in ("QB", "RB", "WR", "TE"):
        grp = sorted((p for p in roster if p.get("pos") == pos), key=lambda x: -(x.get("season") or 0))
        n = len(grp)
        starters_here = [p for p in grp if id(p) in start_ids]
        ranks = [pool_rank.get((pos, p.get("name"))) for p in starters_here if pool_rank.get((pos, p.get("name")))]
        avg_rank = (sum(ranks) / len(ranks)) if ranks else None
        # rank-per-starter thresholds (a startable RB is ~top-24, elite ~top-8)
        starters_needed = _START[pos]
        good_cut = {"QB": 8, "RB": 18, "WR": 24, "TE": 8}[pos]
        elite_cut = {"QB": 4, "RB": 8, "WR": 10, "TE": 4}[pos]
        if avg_rank is None:
            pgrade = "F"
        elif avg_rank <= elite_cut:
            pgrade = "A"
        elif avg_rank <= good_cut:
            pgrade = "B"
        elif avg_rank <= good_cut * 1.8:
            pgrade = "C"
        else:
            pgrade = "D"
        thin = n < _DEPTH_TARGET[pos]
        positions[pos] = {"count": n, "grade": pgrade, "avg_starter_rank":
                          round(avg_rank, 1) if avg_rank else None, "thin": thin,
                          "best": grp[0]["name"] if grp else None}
        if pgrade in ("A",) and not thin:
            strengths.append(f"Strong at {pos} — your starters are top-tier (best: {grp[0]['name']})")
        if thin:
            weaknesses.append(f"Thin at {pos}: only {n} (want {_DEPTH_TARGET[pos]}+) — risky across byes/injuries")
        elif pgrade in ("D", "F"):
            weaknesses.append(f"Weak {pos} room — your starters here are below replacement")

    # Stacks: a QB with same-team pass-catchers (correlated ceiling).
    stacks = []
    qbs = [p for p in roster if p.get("pos") == "QB"]
    catchers = [p for p in roster if p.get("pos") in ("WR", "TE")]
    for qb in qbs:
        mates = [c["name"] for c in catchers if c.get("team") and c.get("team") == qb.get("team")]
        if mates:
            stacks.append({"qb": qb["name"], "team": qb.get("team"), "partners": mates})
    # Modifiers
    if stacks:
        score = min(100, score + 3 * sum(len(s["partners"]) for s in stacks))
        strengths.append("Has stacks: " + "; ".join(f"{s['qb']} + {', '.join(s['partners'])}" for s in stacks))
    else:
        weaknesses.append("No QB↔pass-catcher stacks — you're leaving correlated ceiling on the table")
    # ceiling read (best ball lives on boom weeks)
    boom_total = sum((p.get("boom") or 0) for p in roster)
    if ceil >= par_mean * 0.45:        # ceiling lineup is a big share of the score
        strengths.append("High weekly ceiling — lots of boom/spike weeks, exactly what wins best ball")
    rookies = [p["name"] for p in roster if p.get("rookie")]
    if len(rookies) >= 4:
        weaknesses.append(f"{len(rookies)} rookies — high upside but boom/bust; make sure you have a stable base")

    score = round(score)
    return {
        "grade": _letter(score), "score": score,
        "proj_week": round(proj, 1), "ceiling_week": round(ceil, 1),
        "boom_total": round(boom_total, 1),
        "n_players": len(roster), "counts": counts,
        "positions": positions, "stacks": stacks,
        "starters": [{"name": p["name"], "pos": p["pos"], "team": p.get("team"),
                      "fppg": p.get("fppg"), "boom": p.get("boom")} for p in starters],
        "strengths": strengths[:5], "weaknesses": weaknesses[:5],
        "vs_field": round((ratio - 1) * 100, 1),    # % better/worse than a chalk team
    }


def grade_names(names):
    """Grade a roster given player NAMES (for the standalone grader). Resolves each
    name against the projection pool; returns (grade, unmatched names)."""
    b = board()
    pool = (b or {}).get("pool") or []
    if not pool:
        return None, []
    idx = {}
    for p in pool:
        idx[_gkey(p.get("name"))] = p
    roster, unmatched = [], []
    for nm in names:
        p = idx.get(_gkey(nm))
        if p:
            roster.append(p)
        elif nm.strip():
            unmatched.append(nm.strip())
    g = grade_roster(roster, pool)
    g["unmatched"] = unmatched
    g["matched"] = [p["name"] for p in roster]
    return g, unmatched


def _gkey(name):
    import unicodedata
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return "".join(c for c in s.lower() if c.isalnum())


# ---- Cached board for the draft room / DFS UI ------------------------------
import threading as _threading
import time as _time
_inflight = [False]


def board():
    """Cached projection pool. NON-BLOCKING: serves the cached sim if fresh, else
    computes it in the background and returns None until ready."""
    key = ("nfl_fantasy_board",)
    hit = nfl_awards.racing._form_cache.get(key)
    if hit and (_time.time() - hit[0]) < 24 * 3600 and hit[1] is not None:
        return hit[1]
    if not _inflight[0]:
        _inflight[0] = True

        def _bg():
            try:
                nfl_awards.racing._cached(key, 24 * 3600, lambda: project(n_seasons=3000))
            finally:
                _inflight[0] = False
        _threading.Thread(target=_bg, daemon=True).start()
    return None
