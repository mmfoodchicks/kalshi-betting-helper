"""NFL player production pool + projection helpers for the fantasy season sim.

  - candidates(): the relevant player pool from the last few seasons' stat leaders,
    each carrying a RECENCY-WEIGHTED full stat line (ESPN core API). Pulling the
    whole per-athlete line (not just the stat they led) keeps secondary production
    -- a back's catches, a receiver's rushing -- from being zeroed.
  - _proj(): regress a player's observed line toward the positional baseline,
    per-stat (passing sticky, sacks/INTs volatile).
  - _team_wins(): projected wins per team (from the cached NFL team board), used to
    scale rookie landing spots and offensive environment.

These feed nfl_sim, which Monte-Carlos the fantasy season off them.
"""

import concurrent.futures as _cf

import pro_data
import racing
import errlog

CORE = "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
# ESPN leader category -> our stat key. (interceptions here = DEFENSIVE picks.)
_CATS = {
    "passingYards": "pass_yds", "passingTouchdowns": "pass_td",
    "rushingYards": "rush_yds", "rushingTouchdowns": "rush_td",
    "receivingYards": "rec_yds", "receivingTouchdowns": "rec_td", "receptions": "rec",
    "sacks": "sacks", "interceptions": "def_int", "totalTackles": "tackles",
}
_TOP = 40            # leaders per category to consider


def _get(u):
    return racing._get_json(u)


# A player's per-season stat line by (ESPN category, stat) -> our key. Unlike the
# leaderboards (which carry only the ONE stat a player led), this captures a whole
# line, so a back's receptions or a receiver's rushing are never zeroed just
# because they weren't top-40 in that category.
_STAT_MAP = {
    ("passing", "passingYards"): "pass_yds", ("passing", "passingTouchdowns"): "pass_td",
    ("passing", "interceptions"): "pass_int",
    ("rushing", "rushingYards"): "rush_yds", ("rushing", "rushingTouchdowns"): "rush_td",
    ("receiving", "receivingYards"): "rec_yds", ("receiving", "receivingTouchdowns"): "rec_td",
    ("receiving", "receptions"): "rec",
    ("defensive", "sacks"): "sacks", ("defensive", "totalTackles"): "tackles",
    ("defensiveInterceptions", "interceptions"): "def_int",
}


def _athlete_season_stats(pid, yr):
    """Full stat line for one athlete-season (every category, not just the ones they
    led). None on failure."""
    try:
        s = _get(f"{CORE}/seasons/{yr}/types/2/athletes/{pid}/statistics?lang=en")
    except Exception:
        return None
    out = {}
    for c in (s.get("splits", {}) or {}).get("categories", []):
        cn = c.get("name")
        for st in c.get("stats", []):
            key = _STAT_MAP.get((cn, st.get("name")))
            if key is not None:
                try:
                    out[key] = float(st.get("value") or 0)
                except (TypeError, ValueError):
                    pass
    return out


def candidates(prior_season, n_back=2):
    """{pid: {name, pos, team_abbr, stats{...}}} — the award pool built from the
    last (1 + n_back) seasons' stat leaders. Each player's production is a RECENCY-
    WEIGHTED average of their FULL stat line across the seasons they were a leader,
    so perennial elites stay elite through a down year and secondary production (a
    back's catches, a receiver's rushing) is captured rather than zeroed."""
    def build():
        teams = {t["id"]: t for t in pro_data.teams("nfl")}
        seasons = list(range(prior_season - n_back, prior_season + 1))
        sw = {yr: i + 1 for i, yr in enumerate(seasons)}      # recency weight, recent highest

        # 1) Which athletes matter, and in which seasons they were a top-40 producer.
        ath_ref, seen = {}, {}
        for yr in seasons:
            try:
                d = _get(f"{CORE}/seasons/{yr}/types/2/leaders?lang=en")
            except Exception:
                continue
            for c in d.get("categories", []):
                if c.get("name") not in _CATS:
                    continue
                for ld in c.get("leaders", [])[:_TOP]:
                    ref = (ld.get("athlete") or {}).get("$ref")
                    if not ref:
                        continue
                    pid = ref.split("/athletes/")[-1].split("?")[0]
                    ath_ref[pid] = ref
                    seen.setdefault(pid, set()).add(yr)

        # 2) Pull each player's FULL line for the seasons they were a leader and
        #    recency-weight-average it (only their productive years, so a genuine
        #    breakout isn't diluted by pre-breakout zeros).
        acc = {pid: {} for pid in seen}
        tasks = [(pid, yr) for pid, yrs in seen.items() for yr in yrs]

        def fetch(t):
            pid, yr = t
            return pid, yr, _athlete_season_stats(pid, yr)
        with _cf.ThreadPoolExecutor(max_workers=12) as ex:
            for pid, yr, st in ex.map(fetch, tasks):
                if not st:
                    continue
                w = sw[yr]
                for k, v in st.items():
                    a = acc[pid].setdefault(k, [0.0, 0.0])
                    a[0] += w * v
                    a[1] += w
        stats = {pid: {k: v[0] / v[1] for k, v in d.items() if v[1]} for pid, d in acc.items()}

        # 3) resolve each unique athlete (name, pos, team, experience) in parallel
        def resolve(item):
            pid, ref = item
            try:
                a = _get(ref)
            except Exception:
                return None
            tref = (a.get("team") or {}).get("$ref") or ""
            tid = tref.split("/teams/")[-1].split("?")[0] if tref else None
            return pid, {
                "id": pid, "name": a.get("displayName"),
                "pos": (a.get("position") or {}).get("abbreviation", ""),
                "team_id": tid, "team_abbr": (teams.get(tid) or {}).get("abbrev"),
                "stats": stats.get(pid, {}),
            }
        out = {}
        with _cf.ThreadPoolExecutor(max_workers=8) as ex:
            for r in ex.map(resolve, ath_ref.items()):
                if r and r[1]["name"]:
                    out[r[0]] = r[1]
        return out
    return racing._cached(("nfl_award_cands_my", prior_season, n_back), 7 * 86400, build) or {}


# ---- Projection ------------------------------------------------------------
# Positional baselines we regress last season's production toward (year-to-year
# mean reversion). Rough league-typical lines for a relevant starter.
_BASE = {"pass_yds": 3700, "pass_td": 22, "pass_int": 11, "rush_yds": 650, "rush_td": 6,
         "rec_yds": 750, "rec_td": 5, "rec": 60, "sacks": 6.0, "def_int": 2, "tackles": 70}
# Per-stat regression: how much to trust last season vs the baseline. Passing
# production is sticky (elite QBs stay elite -> light regression), sacks/INTs are
# volatile year-to-year (regress hard), rushing/receiving in between. This keeps
# real QB tiers intact for MVP while stopping a one-off sack title from dominating.
_REG = {"pass_yds": 0.82, "pass_td": 0.80, "pass_int": 0.55, "rush_yds": 0.68, "rush_td": 0.66,
        "rec_yds": 0.72, "rec_td": 0.66, "rec": 0.74, "sacks": 0.55, "def_int": 0.48,
        "tackles": 0.74}


def _team_wins():
    """{team_abbr: projected wins} from the cached NFL team projection."""
    try:
        import deep_cache
        board, _ = deep_cache.load("nfl")
        if board and board.get("teams"):
            return {t.get("abbrev") or t.get("abbr"): t.get("proj_wins", 8.5)
                    for t in board["teams"]}
    except Exception as _e:
        errlog.note("NFLA-team_wins", _e)
    return {}


def _proj(stats):
    """Regress a player's observed stats toward the positional baseline, per-stat."""
    return {k: _REG[k] * stats.get(k, 0) + (1 - _REG[k]) * (_BASE[k] if stats.get(k, 0) else 0)
            for k in _BASE}
