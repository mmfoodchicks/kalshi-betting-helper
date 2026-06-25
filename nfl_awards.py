"""NFL award projections (MVP / OPOY / DPOY / OROY / DROY) via a production sim +
history-calibrated voting model — the approach Madden's season sim uses, with our
'ratings' coming from real prior-season production instead of subjective scouting.

Pipeline:
  1. candidates(): the award-relevant player pool from last season's stat leaders,
     each with their production + current team + rookie flag (ESPN core API).
  2. project/simulate: each player's season line is sampled with year-to-year
     variance, injury (games-played) risk, and a boost tied to their team's
     projected success (a QB on a winning team throws more TDs) — the team rating
     comes from pro_sim. Many simulated seasons -> a distribution per player.
  3. vote(): a per-award scoring model calibrated to history (MVP ≈ a QB on a top
     team; DPOY ≈ sacks/INTs + team defence; ROY ≈ best rookie production). Each
     simulated season has one winner per award; we count how often each player
     wins -> award odds.
"""

import concurrent.futures as _cf
import math
import random

import pro_data
import pro_sim
import racing

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


def candidates(prior_season, n_back=2):
    """{pid: {name, pos, team_abbr, stats{...}}} — the award pool built from the
    last (1 + n_back) seasons' stat leaders. Each player's production is a RECENCY-
    WEIGHTED average across the years they appear, so perennial elites stay elite
    through a down/injured year and a one-season spike is tempered — the same
    multi-year idea that fixed the racing specialists."""
    def build():
        teams = {t["id"]: t for t in pro_data.teams("nfl")}
        seasons = list(range(prior_season - n_back, prior_season + 1))
        sw = {yr: i + 1 for i, yr in enumerate(seasons)}      # recency weight, recent highest

        # 1) per (pid, stat): recency-weighted average across the years present
        ath_ref = {}
        acc = {}   # pid -> {stat: [weighted_sum, weight]}
        for yr in seasons:
            try:
                d = _get(f"{CORE}/seasons/{yr}/types/2/leaders?lang=en")
            except Exception:
                continue
            for c in d.get("categories", []):
                key = _CATS.get(c.get("name"))
                if not key:
                    continue
                for ld in c.get("leaders", [])[:_TOP]:
                    ref = (ld.get("athlete") or {}).get("$ref")
                    if not ref:
                        continue
                    pid = ref.split("/athletes/")[-1].split("?")[0]
                    ath_ref[pid] = ref
                    a = acc.setdefault(pid, {}).setdefault(key, [0.0, 0.0])
                    a[0] += sw[yr] * float(ld.get("value") or 0)
                    a[1] += sw[yr]
        stats = {pid: {k: v[0] / v[1] for k, v in d.items() if v[1]} for pid, d in acc.items()}

        # 2) resolve each unique athlete (name, pos, team, experience) in parallel
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


# ---- Projection + season Monte Carlo + voting model ------------------------
# Positional baselines we regress last season's production toward (year-to-year
# mean reversion). Rough league-typical lines for a relevant starter.
_BASE = {"pass_yds": 3700, "pass_td": 22, "rush_yds": 650, "rush_td": 6,
         "rec_yds": 750, "rec_td": 5, "rec": 60, "sacks": 6.0, "def_int": 2, "tackles": 70}
# Per-stat regression: how much to trust last season vs the baseline. Passing
# production is sticky (elite QBs stay elite -> light regression), sacks/INTs are
# volatile year-to-year (regress hard), rushing/receiving in between. This keeps
# real QB tiers intact for MVP while stopping a one-off sack title from dominating.
_REG = {"pass_yds": 0.82, "pass_td": 0.80, "rush_yds": 0.68, "rush_td": 0.66,
        "rec_yds": 0.72, "rec_td": 0.66, "rec": 0.74, "sacks": 0.55, "def_int": 0.48,
        "tackles": 0.74}
_FORM_SD = 0.24      # season-to-season production swing (breakout / decline / scheme)
_VOTE_SD = 0.13      # raw voter unpredictability on top of production


def _team_wins():
    """{team_abbr: projected wins} from the cached NFL team projection."""
    try:
        import deep_cache
        board, _ = deep_cache.load("nfl")
        if board and board.get("teams"):
            return {t.get("abbrev") or t.get("abbr"): t.get("proj_wins", 8.5)
                    for t in board["teams"]}
    except Exception:
        pass
    return {}


def _proj(stats):
    """Regress a player's observed stats toward the positional baseline, per-stat."""
    return {k: _REG[k] * stats.get(k, 0) + (1 - _REG[k]) * (_BASE[k] if stats.get(k, 0) else 0)
            for k in _BASE}


def _mvp_score(p, line, team_wins):
    """MVP ≈ a QB on a top team. Production scaled by a steep team-wins factor;
    non-QBs carry a heavy structural penalty (only a handful of non-QB MVPs ever)."""
    wf = max(0.15, (team_wins - 5.0) / 7.0) ** 1.3            # team wins matter, less steeply
    if p["pos"] == "QB":
        prod = line["pass_yds"] * 0.011 + line["pass_td"] * 3.2 + \
            line["rush_yds"] * 0.04 + line["rush_td"] * 4.0
        return prod * wf
    if p["pos"] in ("RB", "WR"):
        prod = line["rush_yds"] * 0.011 + line["rush_td"] * 4 + \
            line["rec_yds"] * 0.009 + line["rec_td"] * 3.5
        return prod * wf * 0.35                                # structural non-QB penalty
    return 0.0


def _off_score(p, line, team_wins):
    """OPOY: best offensive producer, only lightly team-dependent."""
    wf = 0.8 + 0.04 * max(0, team_wins - 8)
    if p["pos"] == "QB":
        prod = line["pass_yds"] * 0.010 + line["pass_td"] * 3.0 + line["rush_td"] * 3.0
    elif p["pos"] in ("RB", "WR", "TE"):
        prod = (line["rush_yds"] + line["rec_yds"]) * 0.011 + \
            (line["rush_td"] + line["rec_td"]) * 3.2
    else:
        return 0.0
    return prod * wf


def _def_score(p, line, team_wins):
    """DPOY ≈ sacks/INTs + a nod to playing on a good team."""
    if p["pos"] in ("QB", "RB", "WR", "TE", "C", "G", "OT", "K", "P"):
        return 0.0
    wf = 0.85 + 0.03 * max(0, team_wins - 8)
    return (line["sacks"] * 6.0 + line["def_int"] * 5.0 + line["tackles"] * 0.35) * wf


def simulate(prior_season=2025, n=20000, seed=None):
    """Monte-Carlo the award races; return per-award ranked win odds."""
    cands = candidates(prior_season)
    if not cands:
        return None
    tw = _team_wins()
    lg_win = 8.5
    rng = random.Random(seed)
    proj = {pid: _proj(p["stats"]) for pid, p in cands.items()}
    mvp = {}; opoy = {}; dpoy = {}
    for pid in cands:
        mvp[pid] = opoy[pid] = dpoy[pid] = 0

    for _ in range(n):
        # one team-win draw per team, shared by that team's players (correlation)
        wsamp = {}

        def team_w(ab):
            if ab not in wsamp:
                wsamp[ab] = max(0.0, min(17.0, rng.gauss(tw.get(ab, lg_win), 2.4)))
            return wsamp[ab]
        best_mvp = best_dpoy = (None, -1.0)
        off_scores = []
        for pid, p in cands.items():
            games = 17.0 * max(0.25, min(1.0, rng.gauss(0.92, 0.13)))   # injury risk
            form = math.exp(rng.gauss(0.0, _FORM_SD))                    # year-to-year swing
            f = games / 17.0 * form
            line = {k: v * f for k, v in proj[pid].items()}
            w = team_w(p["team_abbr"])
            vote = math.exp(rng.gauss(0.0, _VOTE_SD))                    # voter unpredictability
            ms = _mvp_score(p, line, w) * vote
            if ms > best_mvp[1]:
                best_mvp = (pid, ms)
            ds = _def_score(p, line, w) * vote
            if ds > best_dpoy[1]:
                best_dpoy = (pid, ds)
            os_ = _off_score(p, line, w) * vote
            if os_ > 0:
                off_scores.append((os_, pid))
        if best_mvp[0]:
            mvp[best_mvp[0]] += 1
        if best_dpoy[0]:
            dpoy[best_dpoy[0]] += 1
        # OPOY rarely goes to the MVP -> award it to the best offensive player who
        # ISN'T the MVP (a realistic voter split).
        off_scores.sort(reverse=True)
        opoy_win = next((pid for _, pid in off_scores if pid != best_mvp[0]), None)
        if opoy_win:
            opoy[opoy_win] += 1

    def board(counter):
        rows = [{"id": pid, "name": cands[pid]["name"], "pos": cands[pid]["pos"],
                 "team": cands[pid]["team_abbr"], "pct": round(100 * c / n, 1)}
                for pid, c in counter.items() if c]
        rows.sort(key=lambda r: -r["pct"])
        return rows[:15]
    return {"sport": "nfl_awards", "n_sims": n, "season": prior_season + 1,
            "mvp": board(mvp), "opoy": board(opoy), "dpoy": board(dpoy)}



# ---- Cached board + live Kalshi pricing ------------------------------------
import threading as _threading
import time as _time
_inflight = [False]

# Kalshi award series -> our board key (markets open through the offseason).
_MARKETS = {"mvp": ["KXNFLMVP"], "opoy": ["KXNFLOPOY"], "dpoy": ["KXNFLDPOY"]}


def _norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join("".join(c for c in s.lower() if c.isalnum() or c == " ").split())


def _price(board):
    """Attach Kalshi yes-ask + edge (model% − ask) to each award's candidates."""
    import kalshi
    for award, series_list in _MARKETS.items():
        rows = board.get(award) or []
        mkt = {}
        for st in series_list:
            try:
                d = kalshi._get_json(f"{kalshi.BASE}/markets?series_ticker={st}&status=open&limit=400")
            except Exception:
                continue
            for m in d.get("markets", []):
                nm = _norm(m.get("yes_sub_title"))
                if nm:
                    mkt[nm] = kalshi._cents(m.get("yes_ask_dollars"))
                    mkt.setdefault(nm.split()[-1], mkt[nm])
        for r in rows:
            c = mkt.get(_norm(r["name"])) or mkt.get(_norm(r["name"]).split()[-1])
            r["kalshi_cents"] = c
            r["edge"] = round(r["pct"] - c, 1) if c is not None else None
    return board


def board():
    """Cached award board with live Kalshi prices. NON-BLOCKING: serves the cached
    sim if fresh, else computes it in the background and returns None until ready."""
    import datetime
    key = ("nfl_awards_board",)
    hit = racing._form_cache.get(key)
    if hit and (_time.time() - hit[0]) < 12 * 3600 and hit[1] is not None:
        return _price(dict(hit[1]))                  # prices fresh each call
    if not _inflight[0]:
        _inflight[0] = True

        def _bg():
            try:
                prior = datetime.date.today().year - 1
                racing._cached(key, 12 * 3600, lambda: simulate(prior, 20000))
            finally:
                _inflight[0] = False
        _threading.Thread(target=_bg, daemon=True).start()
    return None
