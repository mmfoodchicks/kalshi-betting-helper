"""C3: the season simulators against the analytic resolution. RESEARCH, read-only (accuracy_gaps.md 13.2).

THE RULE, as registered (research/reports/accuracy_gaps.md section 13.2, 2026-10-29) -- quoted, not changed:

  "C3 the season simulators. Replay 2024 and 2025 week by week: the college and NFL season sims' projected
  win totals, division/playoff and CFP/title probabilities from the compressed engine against the analytic
  resolution (Phi(margin / 18) college, Phi(margin / 13.2) NFL), scored on real win totals (MAE) and playoff
  outcomes (log loss), clustered by team; the CFP field from the committee's own latest ranking (ESPN's
  rankings feed) from the week it exists, tested on 2024-25 field membership. Pass: the analytic resolution
  wins both seasons by > 2 SE."

Section 1's C3 (item 5) says the same and adds "ship the resolution that wins both seasons by 2 SE
(clustered by team). For the CFP field, the committee's own last ranking as the ranking metric from the week
it exists, tested on 2024-25 bracket membership (24 slots)."

SETTLEMENTS. Every ambiguity the rule leaves open, settled here and committed (this file and
research/data/c3_season/inputs.json) BEFORE the replay or the scoring ran -- the git log shows the order.
Nothing below was chosen after seeing a score; the scoring stage runs once (S10).

 S1 Data. ESPN only, the endpoints the production season sims read: college scoreboards
    `scoreboard?seasontype=2&week=W&dates=S&groups=80`, W = 1-15 (cfb.schedule's sweep, cfb._REG_WEEKS); the
    FBS standings tree of season S-1 (cfb.teams(S): the prior), of season S (cfb.teams(S + 1): conference
    membership ONLY, never a record) and S-1's FCS tree (a prior for a school new to FBS); NFL scoreboards
    seasontype=2 weeks 1-18 (nfl_season.full_schedule). Outcomes: the NFL standings of season S
    (`playoffSeed`), college's postseason scoreboard (the "College Football Playoff" games: the 12-team field
    and the National Championship's winner). The committee ranking: ESPN core `rankings/21` ("Playoff
    Committee Rankings") per week, with its own release `date`. `fetch` writes one compact file,
    research/data/c3_season/inputs.json (ids, weeks, scores, conferences, priors, rankings, outcomes -- no
    ESPN payload), so the replay is offline and the guards read the same file. Not used: Kalshi's win-total
    ladders (no 2024/25 history anywhere in the repo; sim-history's nightly history starts in 2026) and the
    deep store's cached seasons (they live on the server and the PC, not in this checkout; the deep store
    carries play-by-play, the season sims read ESPN's scoreboards).
 S2 As of. The projection "before week w" locks every result of ESPN week < w and plays every game of week
    >= w; its ratings read results of week < w only. A college conference championship game enters the
    schedule only from its own week (its pairing is not known before the last regular weekend; production's
    sweep sees TBD placeholders until then and skips them). A committee ranking is usable at week w only if
    its release date (ESPN's stamp, 08:00Z on the Tuesday it is announced that evening) is before week w's
    first kickoff -- a Tuesday MAC game opens week 11 in both seasons, so the week's projection is read as the
    Tuesday one; no result of week w is read either way. A game postponed into a later ESPN week sits in the
    week it was played; a cancelled game is absent (production drops it once ESPN marks it). Weeks scored:
    NFL 1-18, college 1-15 (a week-19 / week-16 "projection" would be the regular season already played).
 S3 The two arms, on identical inputs. One season routine per sport, the arm a parameter: the same
    schedule, teams, as-of ratings, banked results, conference champions' rule, CFP field rule and bracket,
    and the SAME random draws for everything that is not a game's resolution -- college's per-season team
    form (production's 2.6 points), the FBS-vs-non-FBS 95% draws and the tiebreak draws come from their own
    seeded streams, so both arms see them bit for bit (common random numbers). Only the game's resolution
    differs:
      engine   production's: cfb._rates_for + cfb._play (form_sd 0.33) / nfl_season._matchup_pts +
               _drive_rates + _play, the home side winning iff it outscores the away side (a drawn engine
               game goes to the away side, as production's run_season reads it);
      analytic the home side wins with probability Phi(margin / sigma), sigma 18 college (cfb._SIGMA) and
               13.2 NFL (nfl_season._wp's), the margin the SAME number the engine is handed: college
               (R_h + form_h) - (R_a + form_a) + 3.0 x host edge (cfb._rates_for's), NFL (R_h - R_a) x 2.25 +
               2.2 x host edge (nfl_season._matchup_pts', not _wp's 2.1 default -- the season sim's number).
    Production plays every regular-season game at host edge +1 (neutral sites ignored) and the postseason at
    its own edges on R without form; both arms keep that. With all three streams one generator and the old
    CFP metric, the engine arm IS production's run_season, draw for draw (a guard pins it, both sports).
    n = 4,000 seasons a week (production's default), seeded crc32("c3|sport|season|week"), the same for
    both arms.
 S4 NFL ratings. Production's preseason level is Kalshi's win-total ladder, which has no 2024/25 history
    (S1), and pro_sim / nfl_awards read today's rosters. The as-of rating is therefore the record-only arm
    the deep validation and game_xref replays used: 8.5 blended toward 8.5 + 0.5 x points differential per
    game at g / (g + 6) (production's own in-season blend, nfl_season._ratings, pf/pa per game rounded to
    0.01 as nfl_live serves them; a team with no win or loss yet stays at its prior, as there). Weeks 1-3
    are close to flat for both arms: that dilutes the test equally and is the price of having no
    historical market. A banked tie counts as the away side's win (production's base_w), both arms.
 S5 College ratings, teams, conferences. Production's prior (cfb.ratings: S-1's FBS differential per game,
    centred on S-1's FBS mean, x (1 - 0.58)) and its Massey fixed point (cfb.inseason_ratings: k 1.5, cap
    50, HFA 3, 25 sweeps) over FBS-vs-FBS finals before week w. The TEAM SET and the CONFERENCES are season
    S's own tree: production reads S-1's tree for both, which in 2024 would have put Texas and Oklahoma in
    the Big 12 and ten departed schools in the Pac-12 -- a defect shared by both arms and not the thing under
    test, so the replay takes the membership the season actually had (known before it began); it is noted
    for the owner in the report. A school new to FBS takes cfb.ratings' cross-division convention: its S-1
    FCS differential centred on the FCS mean, x (1 - 0.58), less _DIV_GAP 27.5. Games against non-FBS
    opponents keep production's flat 0.95 in both arms (not a rated pair; not the resolution under test).
 S6 The CFP field (college, both arms). Production's selection, with the registered change: from the
    first week a committee ranking is usable (S2; week 11 in both seasons) the ranking metric IS the
    committee's latest ranking -- ranks 1-25 in order, every unranked team below every ranked one and
    ordered among themselves by production's metric (0.6 x wins + rating + form); before that week,
    production's metric. Conference champions as production (most simulated conference wins, then
    rating; independents never), the five highest-ranked champions plus seven at-large, production's
    bracket (four champion byes) for the title in both seasons (2025 seeded straight by ranking; that moves
    the title figure only, never membership). Read literally, the committee's ranking does not move with a
    simulated result after it; that is the registered metric, and the report says what it costs. The same
    simulated seasons also select under production's metric alone, so membership is read both ways:
    "tested on 2024-25 field membership" is the SECONDARY comparison committee-vs-production metric (weeks
    with a ranking, membership log loss, by team), reported with the same 2 SE / both-seasons standard. It
    does not decide the resolution, and nothing ships from it in this change.
 S7 Targets. NFL: the 17-game regular-season wins, a tie half a win (scoreboards); division title = playoff
    seed 1-4, playoffs = seed 1-7 (ESPN standings). College: regular-season wins over the replay's own game
    set (weeks 1-15, non-FBS opponents included) EXCLUDING conference championship games, and the
    projection counts its wins the same way (the title game is played in the week-15 projection for the
    champion, never counted as a win) -- so the target is one number all season, not one that grows a game
    in week 15. CFP: the 12 teams in the real playoff; title: the national champion.
 S8 Scores, per team per week, both arms: |E[wins] - real wins|, and the playoff-outcome log loss summed
    over the sport's named outcomes -- NFL division title + playoffs ("division/playoff"), college CFP
    field + national title ("CFP/title") -- each probability read (k + 0.5) / (n + 1) from k of n seasons
    (a zero count is never an infinite loss). The paired difference d = engine - analytic (positive: the
    analytic resolution is better), its mean over all team-weeks of the season, and its SE clustered by
    team (game_xref_common.cluster_se: the between-team variance of team sums), because a team's 15-18
    weekly projections are one forecast revised, not independent draws. NFL pennant and Super Bowl log
    losses are reported beside them, descriptive only.
 S9 The verdict, per sport (the two sims are separate production code paths): PASS iff in 2024 AND in
    2025 the analytic resolution's mean improvement exceeds 2 SE on BOTH named scores (the win-total MAE
    and the playoff-outcome log loss). "Wins ... by > 2 SE" is read conjunctively over the two scores the
    rule names; a sport where the scores split, or either season falls short, is NOT ESTABLISHED and its
    production sim is unchanged. The 95% interval of each improvement (mean +/- 1.96 SE) is the bound
    reported.
 S10 Once. `replay` and `score` refuse a dirty tree for this file and its inputs and stamp HEAD; `score`
    refuses to overwrite a committed result. Each season is scored once.

usage:
  VIGIL_NO_BG=1 python3 research/c3_season_replay.py fetch --cache <dir>      # ESPN -> inputs.json
  VIGIL_NO_BG=1 python3 research/c3_season_replay.py replay [--n 4000] [--procs 4]   # -> c3_season/proj.json
  VIGIL_NO_BG=1 python3 research/c3_season_replay.py score                    # -> research/data/c3_season_replay.json
"""
import datetime
import hashlib
import json
import math
import os
import random
import re
import subprocess
import sys
import zlib
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("VIGIL_NO_BG", "1")

import cfb                         # noqa: E402
import nfl_season                  # noqa: E402

SEASONS = (2024, 2025)
N_SIMS = 4000                      # production's default (cfb.run_season / nfl_season.run_season)
SIGMA = {"cfb": 18.0, "nfl": 13.2}  # the rule's; cfb._SIGMA and nfl_season._wp's divisor
CFB_WEEKS = tuple(range(1, cfb._REG_WEEKS + 1))
NFL_WEEKS = tuple(range(1, 19))
CFB_FORM_SD = 2.6                  # cfb.run_season's local _FORM_SD (a guard reads the source for it)
NFS_PRIOR = 8.5                    # S4: the record-only arm's prior, nfl_season's league-average rating
ARMS = ("engine", "analytic")

DATA = os.path.join(ROOT, "research", "data", "c3_season")
INPUTS = os.path.join(DATA, "inputs.json")
PROJ = os.path.join(DATA, "proj.json")
RESULT = os.path.join(ROOT, "research", "data", "c3_season_replay.json")
_CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football"
_NFL_STAND = "https://site.web.api.espn.com/apis/v2/sports/football/nfl/standings"


def _phi(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# ======================================================================== fetch (S1)
def _install_cache(cache):
    """racing._get_json behind a disk cache, so ESPN is asked once per URL (game_xref's convention)."""
    from research import game_xref_cfb
    return game_xref_cfb.install_cache(cache)


def _cfb_games(get, season):
    """cfb.schedule's parse of the FBS sweep, keeping ids, scores and the title-game flag."""
    seen = {}
    for wk in CFB_WEEKS:
        d = get(f"{cfb._SITE}/scoreboard?seasontype=2&week={wk}&dates={season}&groups={cfb._FBS_GROUP}&limit=400",
                timeout=25) or {}
        for e in d.get("events", []):
            comp = (e.get("competitions") or [{}])[0]
            stype = ((comp.get("status") or {}).get("type")) or {}
            if stype.get("state") != "post" or not stype.get("completed"):
                continue                  # cfb.schedule drops postponed/cancelled; history has no live game
            side = {}
            for c in comp.get("competitors", []):
                try:
                    sc = float(c.get("score"))
                except (TypeError, ValueError):
                    sc = None
                side[c.get("homeAway")] = (str((c.get("team") or {}).get("id") or c.get("id")), sc)
            if "home" not in side or "away" not in side or side["home"][1] is None or side["away"][1] is None:
                continue
            notes = " ".join(str(n.get("headline") or "") for n in comp.get("notes") or [])
            seen[e.get("id")] = {"id": e.get("id"), "week": wk, "date": e.get("date"),
                                 "home": side["home"][0], "away": side["away"][0],
                                 "hs": side["home"][1], "as": side["away"][1],
                                 "neutral": bool(comp.get("neutralSite")),
                                 "ccg": "Championship" in notes}
    return sorted(seen.values(), key=lambda g: (g["week"], g["date"] or "", g["id"]))


def _cfb_rankings(get, season):
    out = []
    for wk in range(1, 18):
        try:
            d = get(f"{_CORE}/seasons/{season}/types/2/weeks/{wk}/rankings/21", timeout=20) or {}
        except Exception:
            continue                      # a week with no committee ranking answers 404
        ranks = sorted(d.get("ranks") or [], key=lambda r: r.get("current") or 99)
        ids = []
        for r in ranks:
            m = re.search(r"/teams/(\d+)", ((r.get("team") or {}).get("$ref")) or "")
            if m:
                ids.append(m.group(1))
        if ids and d.get("date"):
            out.append({"week": wk, "date": d["date"], "ranks": ids})
    return out


def _cfb_postseason(get, season):
    d = get(f"{cfb._SITE}/scoreboard?seasontype=3&dates={season}&groups={cfb._FBS_GROUP}&limit=400", timeout=25) or {}
    field, champ = set(), None
    for e in d.get("events", []):
        comp = (e.get("competitions") or [{}])[0]
        notes = " ".join(str(n.get("headline") or "") for n in comp.get("notes") or [])
        if "College Football Playoff" not in notes:
            continue
        for c in comp.get("competitors", []):
            tid = str((c.get("team") or {}).get("id") or c.get("id"))
            field.add(tid)
            if "National Championship" in notes and c.get("winner"):
                champ = tid
    return sorted(field), champ


def fetch_cfb(get, season):
    now_tree = cfb.teams(season + 1, "fbs")        # standings?season=S: this season's membership (S5)
    prior_fbs = cfb.teams(season, "fbs")           # standings?season=S-1: production's prior source
    prior_fcs = cfb.teams(season, "fcs")
    order = sorted(now_tree, key=int)
    new = [t for t in order if t not in prior_fbs]
    field, champ = _cfb_postseason(get, season)
    return {
        "teams": {t: {"name": now_tree[t]["name"], "abbr": now_tree[t]["abbr"], "conf": now_tree[t]["conf"]}
                  for t in order},
        "order": order,
        "prior_fbs": {t: v["diff_pg"] for t, v in prior_fbs.items()},
        "prior_fcs_new": {t: prior_fcs[t]["diff_pg"] for t in new if t in prior_fcs},
        "fcs_mean": (sum(v["diff_pg"] for v in prior_fcs.values()) / len(prior_fcs)) if prior_fcs else 0.0,
        "games": _cfb_games(get, season),
        "rankings": _cfb_rankings(get, season),
        "cfp_field": field, "champion": champ}


def fetch_nfl(get, season):
    games = []
    for wk in NFL_WEEKS:
        d = get(f"{nfl_season._SITE}/scoreboard?seasontype=2&week={wk}&dates={season}", timeout=15) or {}
        for e in d.get("events", []):
            comp = (e.get("competitions") or [{}])[0]
            cs = comp.get("competitors", [])
            h = next((c for c in cs if c.get("homeAway") == "home"), None)
            a = next((c for c in cs if c.get("homeAway") == "away"), None)
            state = ((comp.get("status") or {}).get("type") or {}).get("state")
            if not h or not a or state != "post":
                continue
            games.append({"id": e.get("id"), "week": wk, "date": e.get("date"),
                          "home": nfl_season._canon(h["team"]["abbreviation"]),
                          "away": nfl_season._canon(a["team"]["abbreviation"]),
                          "hs": float(h.get("score") or 0), "as": float(a.get("score") or 0)})
    st = get(f"{_NFL_STAND}?season={season}", timeout=20) or {}
    seeds = {}
    for conf in st.get("children", []):
        for sub in (conf.get("children") or [conf]):
            for e in sub.get("standings", {}).get("entries", []):
                s = {}
                for x in e.get("stats", []):
                    s.setdefault(x["name"], x.get("value"))
                ab = nfl_season._canon((e.get("team") or {}).get("abbreviation"))
                if s.get("playoffSeed"):
                    seeds[ab] = int(s["playoffSeed"])
    games.sort(key=lambda g: (g["week"], g["date"] or "", g["id"]))
    return {"games": games, "seeds": seeds}


def fetch(cache):
    get = _install_cache(cache)
    out = {"fetched_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "source": "ESPN public JSON (S1)", "cfb": {}, "nfl": {}}
    for s in SEASONS:
        out["cfb"][str(s)] = fetch_cfb(get, s)
        out["nfl"][str(s)] = fetch_nfl(get, s)
    os.makedirs(DATA, exist_ok=True)
    with open(INPUTS, "w") as fh:
        json.dump(out, fh, sort_keys=True, separators=(",", ":"))
    for s in SEASONS:
        c, n = out["cfb"][str(s)], out["nfl"][str(s)]
        print(f"{s}: cfb {len(c['order'])} FBS teams, {len(c['games'])} games, "
              f"{sum(g['ccg'] for g in c['games'])} title games, rankings weeks {[r['week'] for r in c['rankings']]}, "
              f"CFP field {len(c['cfp_field'])}, champion {c['champion']}, new to FBS {sorted(c['prior_fcs_new'])}; "
              f"nfl {len(n['games'])} games, {len(n['seeds'])} seeded")
    return out


def load_inputs(path=INPUTS):
    with open(path) as fh:
        return json.load(fh)


# ======================================================================== as of (S2, S4, S5)
def massey(prior, played):
    """cfb.inseason_ratings' fixed point (k 1.5, cap 50, HFA 3, 25 sweeps); no played game returns the prior
    itself, as production does (k x p / k is not always p to the last bit)."""
    if not played:
        return dict(prior)
    R = dict(prior)
    for _ in range(25):
        acc = {t: 0.0 for t in R}
        cnt = {t: 0 for t in R}
        for g in played:
            h, a = g["home"], g["away"]
            m = max(-cfb._MARGIN_CAP, min(cfb._MARGIN_CAP, g["margin"])) \
                - (0.0 if g.get("neutral") else cfb._HFA_MARGIN)
            acc[h] += m + R[a]; cnt[h] += 1
            acc[a] += -m + R[h]; cnt[a] += 1
        R = {t: (cfb._PRIOR_K * prior[t] + acc[t]) / (cfb._PRIOR_K + cnt[t]) for t in R}
    return R


def cfb_prior(ci):
    """cfb.ratings on this season's team set (S5)."""
    pf = ci["prior_fbs"]
    mean = sum(pf.values()) / len(pf)
    out = {}
    for t in ci["order"]:
        if t in pf:
            out[t] = (pf[t] - mean) * (1 - cfb._REG)
        elif t in ci["prior_fcs_new"]:
            out[t] = (ci["prior_fcs_new"][t] - ci["fcs_mean"]) * (1 - cfb._REG) - cfb._DIV_GAP
        else:
            out[t] = -cfb._DIV_GAP
    return out


def first_kickoff(games, w):
    ds = [g["date"] for g in games if g["week"] == w and g.get("date")]
    return min(ds) if ds else None


def _iso(s):
    return datetime.datetime.strptime(s.replace("Z", ""), "%Y-%m-%dT%H:%M" + (":%S" if s.count(":") > 1 else ""))


def cfb_asof(ci, w):
    """Everything the week-w college projection may read (S2): results of week < w, the schedule of week >= w
    (a title game only from its own week), the ratings from those results, the latest committee ranking
    released before week w's first kickoff."""
    tset = set(ci["order"])
    sched = []
    for g in ci["games"]:
        if g["ccg"] and g["week"] > w:
            continue
        row = {"home": g["home"] if g["home"] in tset else None, "away": g["away"] if g["away"] in tset else None,
               "week": g["week"], "neutral": g["neutral"], "ccg": g["ccg"], "final": g["week"] < w}
        if row["home"] is None and row["away"] is None:
            continue
        if row["final"]:
            row["home_won"] = g["hs"] > g["as"]
            row["margin"] = g["hs"] - g["as"]
        sched.append(row)
    prior = cfb_prior(ci)
    R = massey(prior, [r for r in sched if r["final"] and r["home"] and r["away"]])
    kick = first_kickoff(ci["games"], w)
    ranking, rweek = None, None
    for r in ci["rankings"]:
        if kick and _iso(r["date"]) < _iso(kick):
            ranking, rweek = [t for t in r["ranks"] if t in tset], r["week"]
    return {"sport": "cfb", "week": w, "order": list(ci["order"]),
            "conf": {t: ci["teams"][t]["conf"] for t in ci["order"]},
            "sched": sched, "prior": prior, "R": R, "ranking": ranking, "ranking_week": rweek}


def nfl_asof(ni, w):
    """Everything the week-w NFL projection may read: results of week < w, the schedule, the record-only
    rating (S4) from those results."""
    sched, pf, pa, g, wl = [], defaultdict(float), defaultdict(float), defaultdict(int), defaultdict(int)
    for x in ni["games"]:
        row = {"week": x["week"], "home": x["home"], "away": x["away"], "final": x["week"] < w}
        if row["final"]:
            row["home_won"] = x["hs"] > x["as"]
            h, a = x["home"], x["away"]
            pf[h] += x["hs"]; pa[h] += x["as"]; g[h] += 1
            pf[a] += x["as"]; pa[a] += x["hs"]; g[a] += 1
            if x["hs"] != x["as"]:
                wl[h] += 1; wl[a] += 1
        sched.append(row)
    R = {}
    for ab in nfl_season._DIV_OF:
        r = NFS_PRIOR
        if g[ab] and wl[ab] >= 1:
            live = NFS_PRIOR + (round(pf[ab] / g[ab], 2) - round(pa[ab] / g[ab], 2)) * nfl_season._WINS_PER_PD
            wt = g[ab] / (g[ab] + 6.0)
            r = (1 - wt) * r + wt * live
        R[ab] = r
    return {"sport": "nfl", "week": w, "sched": sched, "R": R}


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


# ======================================================================== the two arms (S3)
def _streams(seed, single):
    if single:                          # production's run_season: one generator for everything
        r = random.Random(seed)
        return r, r, r
    return (random.Random(f"{seed}:form"), random.Random(f"{seed}:misc"), random.Random(f"{seed}:game"))


def cfb_resolver(arm):
    """(rating_home, rating_away, host_edge, rng) -> home wins. The margin is cfb._rates_for's."""
    if arm == "engine":
        def res(x, y, edge, rng):
            rh, ra = cfb._rates_for(x, y, edge)
            ph, pa = cfb._play(rh, ra, rng)
            return ph > pa
    else:
        sig = SIGMA["cfb"]

        def res(x, y, edge, rng):
            return rng.random() < _phi(((x - y) + cfb._HFA_MARGIN * edge) / sig)
    return res


def cfb_bracket(seeds, R, res, rng):
    """cfb._cfp_bracket with the arm's resolver (draw for draw the same under the engine)."""
    if len(seeds) < 12:
        return seeds[0] if seeds else None
    rank = {t: i for i, t in enumerate(seeds)}

    def game(hi, lo, host):
        return hi if res(R.get(hi, 0.0), R.get(lo, 0.0), 1 if host else 0, rng) else lo

    r1 = [game(seeds[4], seeds[11], True), game(seeds[5], seeds[10], True),
          game(seeds[6], seeds[9], True), game(seeds[7], seeds[8], True)]
    r1.sort(key=lambda t: rank[t])
    byes = [seeds[0], seeds[1], seeds[2], seeds[3]]
    q = [game(byes[0], r1[-1], False), game(byes[1], r1[-2], False),
         game(byes[2], r1[-3], False), game(byes[3], r1[-4], False)]
    q.sort(key=lambda t: rank[t])
    s = [game(q[0], q[-1], False), game(q[1], q[-2], False)]
    s.sort(key=lambda t: rank[t])
    return game(s[0], s[1], False)


def _cfp_field(champs, teams, key):
    """cfb.run_season's selection under a ranking key: five top champions, seven at-large, champion byes."""
    top = sorted(champs, key=key, reverse=True)[:5]
    tops = set(top)
    at_large = sorted((t for t in teams if t not in tops), key=key, reverse=True)[:7]
    field = top + at_large
    byes = top[:4]
    bset = set(byes)
    return field, byes + sorted([t for t in field if t not in bset], key=key, reverse=True)


def sim_cfb(A, arm, n, seed, single=False, use_committee=True):
    """One week's college projection under one arm. Returns per-team counts:
    [sum wins (title games excluded, S7), sum wins (all, production's proj_wins), made (primary field),
     made (production's metric), title]."""
    teams, conf_of, R = A["order"], A["conf"], A["R"]
    confs = defaultdict(list)
    for t in teams:
        c = conf_of.get(t)
        if c and "independ" not in c.lower():
            confs[c].append(t)
    base_w, base_cw, remaining = defaultdict(int), defaultdict(int), []
    for g in A["sched"]:
        h, a = g["home"], g["away"]
        same = bool(h and a and conf_of.get(h) and conf_of.get(h) == conf_of.get(a))
        if g["final"] and "home_won" in g:
            if h and a:
                w = h if g["home_won"] else a
                base_w[w] += 1
                if same:
                    base_cw[w] += 1
            elif h or a:
                fbs_t = h or a
                if (h and g["home_won"]) or (a and not g["home_won"]):
                    base_w[fbs_t] += 1
            continue
        remaining.append((h, a, same, g["ccg"]))
    rank = {t: i for i, t in enumerate(A["ranking"] or [])} if use_committee and A.get("ranking") else None
    form_rng, misc, game_rng = _streams(seed, single)
    res = cfb_resolver(arm)
    out = {t: [0, 0, 0, 0, 0] for t in teams}
    form_digest = hashlib.sha256()
    for _ in range(n):
        form = {t: form_rng.gauss(0.0, CFB_FORM_SD) for t in teams}
        form_digest.update(repr(form[teams[0]]).encode())
        wins = defaultdict(int, base_w)
        wnc = defaultdict(int, base_w)
        cwins = defaultdict(int, base_cw)
        for h, a, sc, ccg in remaining:
            if not (h and a):
                fbs_t = h or a
                if fbs_t and misc.random() < 0.95:
                    wins[fbs_t] += 1
                    wnc[fbs_t] += 1
                continue
            w = h if res(R[h] + form[h], R[a] + form[a], 1, game_rng) else a
            wins[w] += 1
            if not ccg:
                wnc[w] += 1
            if sc:
                cwins[w] += 1
        champs = [cfb._conf_champ(m, wins, cwins, R, misc) for m in confs.values() if m]

        def old(t):
            return 0.6 * wins.get(t, 0) + R.get(t, 0.0) + form.get(t, 0.0)
        f_old, s_old = _cfp_field(champs, teams, old)
        if rank is not None:
            f_pri, s_pri = _cfp_field(champs, teams,
                                      lambda t: (1, -rank[t]) if t in rank else (0, old(t)))
        else:
            f_pri, s_pri = f_old, s_old
        for t in teams:
            o = out[t]
            o[0] += wnc.get(t, 0)
            o[1] += wins.get(t, 0)
        for t in f_pri:
            out[t][2] += 1
        for t in f_old:
            out[t][3] += 1
        nc = cfb_bracket(s_pri, R, res, game_rng)
        if nc is not None:
            out[nc][4] += 1
    return {"counts": out, "n": n, "inputs": digest({k: A[k] for k in ("order", "conf", "sched", "R", "ranking")}),
            "form": form_digest.hexdigest()[:16]}


def nfl_prep(arm, ra, rb, edge):
    if arm == "engine":
        ea, eb = nfl_season._matchup_pts(ra, rb, edge)
        return nfl_season._drive_rates(ea), nfl_season._drive_rates(eb)
    margin = (ra - rb) * nfl_season._PTS_PER_WIN + nfl_season._HFA_MARGIN * edge
    return _phi(margin / SIGMA["nfl"])


def nfl_play(arm, prep, rng):
    """True when the side listed first wins."""
    if arm == "engine":
        pa, pb = nfl_season._play(prep[0], prep[1], rng)
        return pa > pb
    return rng.random() < prep


def sim_nfl(A, arm, n, seed, single=False):
    """One week's NFL projection under one arm, nfl_season.run_season's structure draw for draw. Returns
    per-team counts [sum wins, playoffs, division, pennant, super bowl]."""
    R = A["R"]
    DIV_OF, DIVS = nfl_season._DIV_OF, nfl_season._DIVS
    remaining = [(g["home"], g["away"], nfl_prep(arm, R[g["home"]], R[g["away"]], +1)) for g in A["sched"]
                 if not g.get("final") and g["home"] in DIV_OF and g["away"] in DIV_OF]
    base_w = defaultdict(int)
    for g in A["sched"]:
        if g.get("final"):
            base_w[g["home"] if g["home_won"] else g["away"]] += 1
    _f, misc, game_rng = _streams(seed, single)
    rnd = misc.random
    confs = {"AFC": [d for d in DIVS if d.startswith("AFC")], "NFC": [d for d in DIVS if d.startswith("NFC")]}
    cache = {}

    def series(a, b, host):
        edge = 1 if host == a else (-1 if host == b else 0)
        k = (a, b, edge)
        if k not in cache:
            cache[k] = nfl_prep(arm, R[a], R[b], edge)
        return a if nfl_play(arm, cache[k], game_rng) else b

    out = {ab: [0, 0, 0, 0, 0] for ab in DIV_OF}
    for _ in range(n):
        wins = dict(base_w)
        for home, away, prep in remaining:
            w = home if nfl_play(arm, prep, game_rng) else away
            wins[w] = wins.get(w, 0) + 1
        for ab in DIV_OF:
            out[ab][0] += wins.get(ab, 0)
        finalists = {}
        for conf, divs in confs.items():
            champs, rest = [], []
            for d in divs:
                members = sorted(DIVS[d], key=lambda ab: (wins.get(ab, 0), rnd()), reverse=True)
                champs.append(members[0])
                rest.extend(members[1:])
            champs.sort(key=lambda ab: (wins.get(ab, 0), rnd()), reverse=True)
            wc = sorted(rest, key=lambda ab: (wins.get(ab, 0), rnd()), reverse=True)[:3]
            seeds = champs + wc
            for ab in champs:
                out[ab][2] += 1
            for ab in seeds:
                out[ab][1] += 1
            w27 = series(seeds[1], seeds[6], seeds[1])
            w36 = series(seeds[2], seeds[5], seeds[2])
            w45 = series(seeds[3], seeds[4], seeds[3])
            alive = sorted((w27, w36, w45), key=seeds.index)
            d1 = series(seeds[0], alive[-1], seeds[0])
            d2 = series(alive[0], alive[1], alive[0])
            hi, lo = (d1, d2) if seeds.index(d1) < seeds.index(d2) else (d2, d1)
            champ = series(hi, lo, hi)
            out[champ][3] += 1
            finalists[conf] = champ
        if len(finalists) == 2:
            out[series(finalists["AFC"], finalists["NFC"], None)][4] += 1
    return {"counts": out, "n": n, "inputs": digest({k: A[k] for k in ("sched", "R")}), "form": None}


def seed_for(sport, season, week):
    return zlib.crc32(f"c3|{sport}|{season}|{week}".encode())


# ======================================================================== replay
def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def _clean_or_die():
    dirty = _git("status", "--porcelain", "--", os.path.relpath(__file__, ROOT), os.path.relpath(INPUTS, ROOT))
    if dirty:
        sys.exit(f"refusing: the harness or its inputs are not committed ({dirty}) -- S10")
    return _git("rev-parse", "HEAD")


_INP = None


def _job(spec):
    sport, season, week, arm, n = spec
    if sport == "cfb":
        A = cfb_asof(_INP["cfb"][str(season)], week)
        r = sim_cfb(A, arm, n, seed_for(sport, season, week))
        r["ranking_week"] = A["ranking_week"]
    else:
        A = nfl_asof(_INP["nfl"][str(season)], week)
        r = sim_nfl(A, arm, n, seed_for(sport, season, week))
    return spec, r


def replay(n=N_SIMS, procs=4):
    global _INP
    head = _clean_or_die()
    _INP = load_inputs()
    jobs = [(sp, s, w, arm, n) for sp, weeks in (("cfb", CFB_WEEKS), ("nfl", NFL_WEEKS))
            for s in SEASONS for w in weeks for arm in ARMS]
    jobs.sort(key=lambda j: (j[0] != "cfb", -len(j)))      # the slow college engine weeks first
    import multiprocessing as mp
    out = {"commit": head, "n": n, "inputs_sha256": hashlib.sha256(open(INPUTS, "rb").read()).hexdigest(),
           "proj": {}}
    with mp.get_context("fork").Pool(procs) as pool:
        for k, ((sport, season, week, arm, _n), r) in enumerate(pool.imap_unordered(_job, jobs), 1):
            out["proj"].setdefault(sport, {}).setdefault(str(season), {}).setdefault(str(week), {})[arm] = r
            print(f"  [{k}/{len(jobs)}] {sport} {season} week {week} {arm}", flush=True)
    with open(PROJ, "w") as fh:
        json.dump(out, fh, sort_keys=True, separators=(",", ":"))
    return out


# ======================================================================== score (S7-S9)
def cluster_se(terms, clusters):
    from research import game_xref_common as C
    return C.cluster_se(terms, clusters)


def ll(p, y):
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def prob(k, n):
    """(k + 0.5) / (n + 1): a zero count is a small probability, never an infinite loss (S8)."""
    return (k + 0.5) / (n + 1.0)


def paired(d, clusters):
    m = sum(d) / len(d)
    se = cluster_se(d, clusters)
    return {"mean": m, "se": se, "z": (m / se) if se and se > 0 else None, "lo95": m - 1.96 * se,
            "hi95": m + 1.96 * se, "n": len(d), "clusters": len(set(clusters))}


def wins_by_2se(st):
    """The registered margin: the improvement (engine - analytic, positive = analytic better) above 2 SE."""
    return st["se"] == st["se"] and st["mean"] > 2.0 * st["se"]


def passes(by_season):
    """One score: {season: paired} -> True only if BOTH registered seasons are present and each wins by > 2 SE."""
    return len(by_season) == len(SEASONS) and all(wins_by_2se(v) for v in by_season.values())


def verdict(per_season):
    """S9: {season: {"mae": paired, "ll": paired}} -> PASS only if every season wins BOTH scores by > 2 SE."""
    ok = passes({s: v["mae"] for s, v in per_season.items()}) and passes({s: v["ll"] for s, v in per_season.items()})
    return "PASS" if ok else "NOT ESTABLISHED"


def real_cfb(ci):
    tset = set(ci["order"])
    wins = defaultdict(float)
    for g in ci["games"]:
        if g["ccg"]:
            continue
        for side, other, won in ((g["home"], g["away"], g["hs"] > g["as"]), (g["away"], g["home"], g["as"] > g["hs"])):
            if side in tset and won:
                wins[side] += 1.0
    field = set(ci["cfp_field"])
    return {t: {"wins": wins.get(t, 0.0), "made": 1.0 if t in field else 0.0,
                "title": 1.0 if t == ci["champion"] else 0.0} for t in ci["order"]}


def real_nfl(ni):
    wins = defaultdict(float)
    for g in ni["games"]:
        if g["hs"] > g["as"]:
            wins[g["home"]] += 1.0
        elif g["as"] > g["hs"]:
            wins[g["away"]] += 1.0
        else:
            wins[g["home"]] += 0.5
            wins[g["away"]] += 0.5
    sd = ni["seeds"]
    return {ab: {"wins": wins.get(ab, 0.0), "playoffs": 1.0 if sd.get(ab, 99) <= 7 else 0.0,
                 "division": 1.0 if sd.get(ab, 99) <= 4 else 0.0} for ab in nfl_season._DIV_OF}


def terms(sport, real, cnt, n):
    """Per-team-week losses for one arm: (abs win error, named-outcome log loss, extras)."""
    if sport == "cfb":
        ew = cnt[0] / n
        lsum = ll(prob(cnt[2], n), real["made"]) + ll(prob(cnt[4], n), real["title"])
        return abs(ew - real["wins"]), lsum, {"cfp": ll(prob(cnt[2], n), real["made"]),
                                              "cfp_old": ll(prob(cnt[3], n), real["made"]),
                                              "title": ll(prob(cnt[4], n), real["title"])}
    ew = cnt[0] / n
    lp, ld = ll(prob(cnt[1], n), real["playoffs"]), ll(prob(cnt[2], n), real["division"])
    return abs(ew - real["wins"]), lp + ld, {"playoffs": lp, "division": ld}


def score_season(sport, season, inp, proj):
    real = real_cfb(inp) if sport == "cfb" else real_nfl(inp)
    weeks = sorted(proj, key=int)
    d_mae, d_ll, cl, wk_of = [], [], [], []
    lv = {arm: {"mae": [], "ll": []} for arm in ARMS}
    extra = defaultdict(lambda: {arm: [] for arm in ARMS})
    sec, sec_cl = {arm: [] for arm in ARMS}, []
    for w in weeks:
        P = proj[w]
        n = P["engine"]["n"]
        for t in sorted(real):
            got = {arm: terms(sport, real[t], P[arm]["counts"][t], n) for arm in ARMS}
            d_mae.append(got["engine"][0] - got["analytic"][0])
            d_ll.append(got["engine"][1] - got["analytic"][1])
            cl.append(t)
            wk_of.append(int(w))
            for arm in ARMS:
                lv[arm]["mae"].append(got[arm][0])
                lv[arm]["ll"].append(got[arm][1])
                for k, v in got[arm][2].items():
                    extra[k][arm].append(v)
            if sport == "cfb" and P["engine"].get("ranking_week"):
                for arm in ARMS:
                    sec[arm].append(got[arm][2]["cfp_old"] - got[arm][2]["cfp"])
                sec_cl.append(t)
    out = {"weeks": [int(w) for w in weeks], "team_weeks": len(d_mae), "teams": len(real),
           "mae": paired(d_mae, cl), "ll": paired(d_ll, cl),
           "levels": {arm: {k: sum(v) / len(v) for k, v in lv[arm].items()} for arm in ARMS},
           "outcomes": {k: {arm: sum(v[arm]) / len(v[arm]) for arm in ARMS} for k, v in extra.items()}}
    phases = ((1, 6), (7, 12), (13, 18)) if sport == "nfl" else ((1, 5), (6, 10), (11, 15))
    out["phases"] = {}
    for lo, hi in phases:
        idx = [i for i, w in enumerate(wk_of) if lo <= w <= hi]
        if idx:
            out["phases"][f"{lo}-{hi}"] = {"mae": paired([d_mae[i] for i in idx], [cl[i] for i in idx]),
                                           "ll": paired([d_ll[i] for i in idx], [cl[i] for i in idx])}
    if sport == "cfb" and sec_cl:
        out["committee_vs_production_metric"] = {arm: paired(sec[arm], sec_cl) for arm in ARMS}
    return out


def score(force=False):
    if os.path.exists(RESULT) and not force:
        sys.exit("refusing: the result is already written -- each season is scored once (S10)")
    head = _clean_or_die()
    inp = load_inputs()
    with open(PROJ) as fh:
        P = json.load(fh)
    res = {"rule": "accuracy_gaps.md 13.2 C3; settlements S1-S10 in research/c3_season_replay.py",
           "harness_commit": P["commit"], "scored_at_commit": head, "n": P["n"],
           "inputs_sha256": P["inputs_sha256"], "sports": {}}
    for sport in ("nfl", "cfb"):
        per = {}
        for s in SEASONS:
            per[str(s)] = score_season(sport, s, inp[sport][str(s)], P["proj"][sport][str(s)])
        v = verdict({s: {"mae": per[s]["mae"], "ll": per[s]["ll"]} for s in per})
        res["sports"][sport] = {"seasons": per, "verdict": v}
        if sport == "cfb":                 # S6's secondary: committee vs production metric, membership only
            res["sports"][sport]["committee_secondary"] = {
                arm: ("committee metric wins both seasons by > 2 SE"
                      if passes({s: per[s]["committee_vs_production_metric"][arm] for s in per})
                      else "not established") for arm in ARMS}
    with open(RESULT, "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
    for sport, r in res["sports"].items():
        for s, x in r["seasons"].items():
            print(f"{sport} {s}: MAE d {x['mae']['mean']:+.4f} +/- {x['mae']['se']:.4f}   "
                  f"LL d {x['ll']['mean']:+.4f} +/- {x['ll']['se']:.4f}")
        print(f"{sport}: {r['verdict']}")
    return res


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("fetch", "replay", "score"))
    ap.add_argument("--cache")
    ap.add_argument("--n", type=int, default=N_SIMS)
    ap.add_argument("--procs", type=int, default=4)
    a = ap.parse_args(argv)
    if a.stage == "fetch":
        if not a.cache:
            sys.exit("fetch needs --cache <dir> (outside the repo)")
        fetch(a.cache)
    elif a.stage == "replay":
        replay(a.n, a.procs)
    else:
        score()


if __name__ == "__main__":
    main(sys.argv[1:])
