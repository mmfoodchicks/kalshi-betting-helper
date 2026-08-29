"""League of Legends esports data + projection model.

Data comes from Leaguepedia (lol.fandom.com) via its public MediaWiki *Cargo*
API -- the wiki that stores a per-game scoreboard for essentially every pro LoL
match. Two tables carry everything we need, and (crucially) they share the same
team/player names, so there's no fragile cross-source mapping:

  - MatchSchedule    -> upcoming matches (Team1, Team2, best-of, tournament, time)
  - ScoreboardPlayers -> per-player, per-MAP line (Champion, K/D/A, CS, role, date)

The Cargo API is rate-limited, so every call is paced + cached, and we pull a
whole TEAM's recent maps in one query (covering all five starters) rather than
querying player-by-player.

The model turns each player's recent per-map K/D/A/CS into projections and DK
Pick 6-style More/Less picks (kills / assists / CS), the way the baseball Pick 6
board works -- esports has no Kalshi combos, so this is a pure prop tool.
"""

import json as _json
import time
import urllib.parse
import urllib.request

_BASE = "https://lol.fandom.com/api.php"
_UA = {"User-Agent": "VigilBot/1.0 (private betting-model research)"}

_cache = {}          # key -> (epoch, value)
_last_call = [0.0]   # gentle client-side rate limiting


def _cached(key, ttl, fn):
    """Cache successes only. `fn` returns None on failure, so a transient
    rate-limit never poisons the cache with an empty result."""
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception:
        val = None
    if val is not None:
        _cache[key] = (time.time(), val)
        return val
    return hit[1] if hit else None


def _cargo(tables, fields, where=None, order_by=None, limit=200):
    """One Cargo query -> list of row dicts on success, or None on failure (error
    or rate-limit after retries) so callers don't cache a bad result. Paced
    (>=2s between calls) with exponential backoff on the wiki's rate limit."""
    gap = time.time() - _last_call[0]
    if gap < 2.5:                    # gentle: the wiki rate-limits bursts hard
        time.sleep(2.5 - gap)
    p = {"action": "cargoquery", "format": "json", "tables": tables,
         "fields": fields, "limit": str(limit)}
    if where:
        p["where"] = where
    if order_by:
        p["order_by"] = order_by
    for attempt in range(4):
        try:
            # urllib, not requests: requests was never in requirements.txt, so
            # the production image didn't have it and this module could not
            # even IMPORT there -- the LoL tab answered 502 while every dev
            # box, with requests installed globally, swore it worked. Caught
            # by CI's first-ever run on a clean environment.
            req = urllib.request.Request(
                _BASE + "?" + urllib.parse.urlencode(p), headers=_UA)
            with urllib.request.urlopen(req, timeout=25) as resp:
                _last_call[0] = time.time()
                d = _json.loads(resp.read().decode("utf-8"))
        except Exception:
            time.sleep(2.0 * (attempt + 1))
            continue
        if isinstance(d, dict) and "error" in d:
            if "rate limit" in (d["error"].get("info", "") or "").lower():
                time.sleep(3.0 * (attempt + 1))
                continue
            return []                                    # genuine query error
        return [x.get("title", {}) for x in (d.get("cargoquery") or [])]
    return None                                          # gave up -> don't cache


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


# ---- Schedule ---------------------------------------------------------------
def _league(page):
    """LCS / LEC / LCK ... from an OverviewPage like 'LCS/2026 Season/Summer'."""
    return (page or "").split("/")[0].strip() or "LoL"


def upcoming(limit=40):
    """The current pro slate: [{team1, team2, bo, league, tournament, dt, date,
    played}], most-recent first. Anchored to Leaguepedia's own timeline (newest
    scheduled matches with real teams), not the wall clock, and TBD bracket
    placeholders are skipped. Cached 15 min."""
    def build():
        rows = _cargo(
            "MatchSchedule",
            "Team1=t1,Team2=t2,BestOf=bo,DateTime_UTC=dt,OverviewPage=pg,Winner=w",
            where='MatchSchedule.Team1 != "TBD" AND MatchSchedule.Team2 != "TBD"',
            order_by="MatchSchedule.DateTime_UTC DESC", limit=limit * 3)
        if rows is None:
            return None
        out = []
        for r in rows:
            t1, t2 = (r.get("t1") or "").strip(), (r.get("t2") or "").strip()
            if not t1 or not t2:
                continue
            out.append({"team1": t1, "team2": t2,
                        "bo": int(_f(r.get("bo"), 1)) or 1,
                        "league": _league(r.get("pg")),
                        "tournament": (r.get("pg") or "").strip(),
                        "dt": r.get("dt"), "date": (r.get("dt") or "")[:10],
                        "played": bool((r.get("w") or "").strip())})
            if len(out) >= limit:
                break
        return out
    return _cached(("lol_upcoming",), 900, build) or []


# ---- Per-team recent maps (covers all five starters in one query) -----------
_ROLE_ORDER = {"Top": 0, "Jungle": 1, "Mid": 2, "Bot": 3, "Support": 4}


def team_players(team, min_maps=2):
    """The team's current starters with their recent per-map lines:
    {player: {role, maps:[{champ,k,d,a,cs,dt}], n, k,d,a,cs (means)}}. One Cargo
    call per team (all players at once), most-recent maps by DESC ordering (not a
    wall-clock window, so it works against Leaguepedia's own timeline). Cached 6h."""
    def build():
        safe = team.replace('"', '')
        rows = _cargo(
            "ScoreboardPlayers",
            "Link=player,Champion=champ,Kills=k,Deaths=d,Assists=a,CS=cs,Role=role,DateTime_UTC=dt",
            where=f'ScoreboardPlayers.Team="{safe}"',
            order_by="ScoreboardPlayers.DateTime_UTC DESC", limit=120)
        if rows is None:
            return None
        by = {}
        for r in rows:
            nm = (r.get("player") or "").strip()
            if not nm:
                continue
            p = by.setdefault(nm, {"role": (r.get("role") or "").strip(), "maps": []})
            p["maps"].append({"champ": (r.get("champ") or "").strip(),
                              "k": _f(r.get("k")), "d": _f(r.get("d")),
                              "a": _f(r.get("a")), "cs": _f(r.get("cs")),
                              "dt": r.get("dt")})
        out = {}
        for nm, p in by.items():
            mp = p["maps"]
            if len(mp) < min_maps:
                continue
            n = len(mp)
            out[nm] = {"role": p["role"], "maps": mp, "n": n,
                       "k": round(sum(m["k"] for m in mp) / n, 2),
                       "d": round(sum(m["d"] for m in mp) / n, 2),
                       "a": round(sum(m["a"] for m in mp) / n, 2),
                       "cs": round(sum(m["cs"] for m in mp) / n, 1)}
        # keep the five most-used players (the current starting roster)
        starters = sorted(out.items(), key=lambda kv: -kv[1]["n"])[:5]
        starters.sort(key=lambda kv: _ROLE_ORDER.get(kv[1]["role"], 9))
        return dict(starters)
    return _cached(("lol_team", team), 6 * 3600, build) or {}


# ---- Projection model -------------------------------------------------------
# Per-map role baselines (pro LoL): kills / assists / CS a role averages. Small
# samples regress toward these so a 3-map call-up isn't taken at face value.
_ROLE_BASE = {
    "Top":     {"k": 2.8, "a": 5.5, "cs": 250.0, "d": 2.5},
    "Jungle":  {"k": 3.2, "a": 7.5, "cs": 200.0, "d": 2.8},
    "Mid":     {"k": 3.8, "a": 6.5, "cs": 270.0, "d": 2.3},
    "Bot":     {"k": 4.2, "a": 6.0, "cs": 290.0, "d": 2.2},
    "Support": {"k": 1.2, "a": 9.5, "cs": 40.0, "d": 2.9},
}
_REG_MAPS = 6          # pseudo-maps of role prior blended into every projection


def _mean(a):
    return sum(a) / len(a) if a else 0.0


def _std(a):
    if len(a) < 2:
        return 0.0
    m = _mean(a)
    return (sum((x - m) ** 2 for x in a) / (len(a) - 1)) ** 0.5


def _reg(samples, base):
    n = len(samples)
    return (n * _mean(samples) + _REG_MAPS * base) / (n + _REG_MAPS) if (n or base) else base


def player_projection(p):
    """Regressed per-map projection for a player from their recent maps."""
    b = _ROLE_BASE.get(p.get("role"), _ROLE_BASE["Mid"])
    kf = [m["k"] for m in p["maps"]]
    af = [m["a"] for m in p["maps"]]
    cf = [m["cs"] for m in p["maps"]]
    df = [m.get("d", b["d"]) for m in p["maps"]]
    return {"role": p.get("role"), "n": len(p["maps"]),
            "kills": round(_reg(kf, b["k"]), 2),
            "assists": round(_reg(af, b["a"]), 2),
            "cs": round(_reg(cf, b["cs"]), 1),
            "deaths": round(_reg(df, b["d"]), 2),
            "kills_std": round(_std(kf) or (b["k"] ** 0.5), 2),
            "assists_std": round(_std(af) or (b["a"] ** 0.5), 2),
            "cs_std": round(_std(cf) or b["cs"] * 0.16, 1),
            "champs": list(dict.fromkeys(m["champ"] for m in p["maps"] if m["champ"]))[:4]}


import math as _math


def _pois_over(mean, line):
    """P(X > line) for X ~ Poisson(mean), line a half-integer."""
    if mean <= 0:
        return 0.0
    k = int(_math.floor(line)) + 1
    cdf, term = 0.0, _math.exp(-mean)
    for i in range(0, k):
        if i > 0:
            term *= mean / i
        cdf += term
    return max(0.0, min(1.0, 1.0 - cdf))


def _norm_over(mean, std, line):
    if std <= 0:
        return 1.0 if mean > line else 0.0
    z = (line - mean) / std
    return max(0.0, min(1.0, 1.0 - 0.5 * (1 + _math.erf(z / _math.sqrt(2)))))


_PICK6_PAYOUT = {"2": 3.0, "3": 6.0, "4": 10.0, "5": 20.0, "6": 25.0}


# ---- Team ratings (Elo) -> match + tournament win probabilities --------------
_ELO_BASE = 1500.0
_ELO_K = 26.0            # per-match update


def elo_map():
    """{team: Elo} from recent match results across every league, in ONE Cargo
    query. Powers both match win% and the tournament futures sim. Cached 6h."""
    def build():
        rows = _cargo(
            "MatchSchedule", "Team1=t1,Team2=t2,Winner=w,DateTime_UTC=dt",
            where='MatchSchedule.Winner != "" AND MatchSchedule.Team1 != "TBD" '
                  'AND MatchSchedule.Team2 != "TBD"',
            order_by="MatchSchedule.DateTime_UTC ASC", limit=800)
        if rows is None:
            return None
        elo = {}
        for r in rows:
            t1, t2, w = (r.get("t1") or "").strip(), (r.get("t2") or "").strip(), r.get("w")
            if not t1 or not t2 or w not in ("1", "2"):
                continue
            ra = elo.setdefault(t1, _ELO_BASE)
            rb = elo.setdefault(t2, _ELO_BASE)
            ea = 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))
            sa = 1.0 if w == "1" else 0.0
            elo[t1] = ra + _ELO_K * (sa - ea)
            elo[t2] = rb + _ELO_K * ((1 - sa) - (1 - ea))
        return elo
    return _cached(("lol_elo",), 6 * 3600, build) or {}


def _game_wp(t1, t2, elo):
    """P(t1 wins a single game) from Elo."""
    ra, rb = elo.get(t1, _ELO_BASE), elo.get(t2, _ELO_BASE)
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def _series_p(p, need):
    """P(a team with per-game win prob p takes a first-to-`need` series."""
    total = 0.0
    for losses in range(need):
        total += _math.comb(need - 1 + losses, losses) * p ** need * (1 - p) ** losses
    return total


def match_winprob(t1, t2, bo, elo=None):
    """P(t1 wins the bo series). Returns a percent."""
    elo = elo if elo is not None else elo_map()
    p = _game_wp(t1, t2, elo)
    need = int(bo) // 2 + 1
    return round(_series_p(p, need) * 100, 1)


# ---- Tournament futures (who wins the split / MSI) --------------------------
import random as _random
from collections import defaultdict as _dd, Counter as _Counter


def tournament_matches(page):
    """Every match for a tournament (OverviewPage): [{t1,t2,bo,winner}]. Cached 30m."""
    def build():
        safe = page.replace('"', '')
        rows = _cargo(
            "MatchSchedule", "Team1=t1,Team2=t2,BestOf=bo,Winner=w,N_MatchInPage=n",
            where=f'MatchSchedule.OverviewPage="{safe}" AND MatchSchedule.Team1 != "TBD" '
                  'AND MatchSchedule.Team2 != "TBD"',
            order_by="MatchSchedule.N_MatchInPage ASC", limit=400)
        if rows is None:
            return None
        out = []
        for r in rows:
            t1, t2 = (r.get("t1") or "").strip(), (r.get("t2") or "").strip()
            if not t1 or not t2:
                continue
            out.append({"t1": t1, "t2": t2, "bo": int(_f(r.get("bo"), 1)) or 1,
                        "winner": r.get("w") if r.get("w") in ("1", "2") else None})
        return out
    return _cached(("lol_tourn", page), 1800, build) or []


def sim_tournament(page, n=4000, playoff_k=6):
    """Monte Carlo a tournament to championship odds: play the remaining matches by
    Elo, seed the top `playoff_k` by record, then a re-seeded single-elim (bo5)
    bracket -> champion. A model estimate -- real playoff formats vary by league."""
    matches = tournament_matches(page)
    if not matches:
        return None
    elo = elo_map()
    teams = sorted(set([m["t1"] for m in matches] + [m["t2"] for m in matches]))
    if len(teams) < 2:
        return None
    base_wins, remaining = {t: 0 for t in teams}, []
    for m in matches:
        if m["winner"] == "1":
            base_wins[m["t1"]] += 1
        elif m["winner"] == "2":
            base_wins[m["t2"]] += 1
        else:
            remaining.append(m)
    made, champ = _dd(int), _dd(int)
    rnd = _random.random
    for _ in range(n):
        wins = dict(base_wins)
        for m in remaining:
            p = _series_p(_game_wp(m["t1"], m["t2"], elo), int(m["bo"]) // 2 + 1)
            wins[m["t1"] if rnd() < p else m["t2"]] += 1
        seeds = sorted(teams, key=lambda t: (wins[t], rnd()), reverse=True)[:playoff_k]
        for t in seeds:
            made[t] += 1
        bracket = list(seeds)
        while len(bracket) > 1:                     # re-seeded single elim (1 vs last)
            nxt, i, j = [], 0, len(bracket) - 1
            while i < j:
                a, b = bracket[i], bracket[j]
                nxt.append(a if rnd() < _series_p(_game_wp(a, b, elo), 3) else b)
                i, j = i + 1, j - 1
            if i == j:
                nxt.append(bracket[i])              # odd bracket -> bye
            bracket = sorted(nxt, key=lambda t: (wins[t], rnd()), reverse=True)
        if bracket:
            champ[bracket[0]] += 1

    def pct(c):
        return round(100.0 * c / n, 1)
    out = [{"team": t, "elo": round(elo.get(t, _ELO_BASE)),
            "record": base_wins[t], "playoffs": pct(made[t]), "champion": pct(champ[t])}
           for t in teams]
    out.sort(key=lambda x: -x["champion"])
    return {"tournament": page, "n_sims": n, "n_teams": len(teams),
            "games_left": len(remaining), "teams": out,
            "note": "Model estimate from Elo + a synthetic top-%d bracket; real playoff "
                    "formats vary by league." % playoff_k}


# ---- Correlated per-map prop simulation -------------------------------------
# Instead of treating each player's kills as an independent Poisson die, this
# actually plays out a map: it draws a SHARED total-kill count for the game and
# splits it between the two teams by the map outcome (a favourite -- and a stomp
# -- takes the larger share), so one team's kills ARE the other's deaths
# (zero-sum). Each team's kills/assists are then handed out among its five
# players by their projected share, and CS rides a shared game-length factor
# (stomps run short -> less farm). The upshot: teammates' stats move together,
# the two teams' kills move opposite, and assists rise and fall with team kills
# -- none of which the old independent model captured. Crucially the marginals
# are centred on each player's regressed projection at a neutral 50/50 game, so
# it stays anchored to the shrinkage model rather than inventing new means.
_SIM_N = 2600            # Monte Carlo maps simulated per matchup
_KILL_FLOOR = 0.3        # keep every player a non-zero share of the kill split


def _alloc(res, names, weights, total, key):
    """Multinomial: hand `total` events to players ~ their weights (C-level)."""
    if total <= 0:
        for x in names:
            res[x][key].append(0)
        return
    c = _Counter(_random.choices(names, weights=weights, k=total))
    for x in names:
        res[x][key].append(c.get(x, 0))


def _emp_over(arr, line):
    """Empirical P(X > line) from a sample."""
    return (sum(1 for x in arr if x > line) / len(arr)) if arr else 0.0


def simulate_map(proj1, proj2, p1, n=_SIM_N):
    """Play `n` maps between two projected rosters; return per-player K/A/CS samples.

    proj1/proj2: {player: player_projection(...)}.  p1: team1's per-map win prob.
    Returns (samples1, samples2), each {player: {"k":[...],"a":[...],"cs":[...]}}.
    """
    names1, names2 = list(proj1), list(proj2)
    kw1 = [max(_KILL_FLOOR, proj1[x]["kills"]) for x in names1]
    kw2 = [max(_KILL_FLOOR, proj2[x]["kills"]) for x in names2]
    aw1 = [max(0.1, proj1[x]["assists"]) for x in names1]
    aw2 = [max(0.1, proj2[x]["assists"]) for x in names2]
    M1, M2 = sum(kw1), sum(kw2)
    A1, A2 = sum(aw1), sum(aw2)
    base_total = M1 + M2 or 1.0
    neutral1 = M1 / base_total                    # kill share in an even game
    apk1 = A1 / M1 if M1 else 1.6                 # this team's own assists-per-kill
    apk2 = A2 / M2 if M2 else 1.6
    edge = abs(p1 - 0.5) * 2.0                    # 0 even .. 1 lopsided
    res1 = {x: {"k": [], "a": [], "cs": []} for x in names1}
    res2 = {x: {"k": [], "a": [], "cs": []} for x in names2}
    gauss, rnd = _random.gauss, _random.random
    for _ in range(n):
        total = max(6, int(round(gauss(base_total, base_total * 0.26))))
        dom = min(0.55, max(0.02, gauss(0.15 + 0.12 * edge, 0.11)))   # map lopsidedness
        share1 = neutral1 + (dom if rnd() < p1 else -dom)
        share1 = min(0.92, max(0.08, share1))
        k1 = int(round(total * share1))
        k2 = max(0, total - k1)
        a1t = int(round(k1 * apk1))               # team assists track team kills
        a2t = int(round(k2 * apk2))
        length = min(1.45, max(0.6, gauss(1.0 - 0.9 * (dom - 0.15), 0.10)))  # stomps short
        _alloc(res1, names1, kw1, k1, "k")
        _alloc(res2, names2, kw2, k2, "k")
        _alloc(res1, names1, aw1, a1t, "a")
        _alloc(res2, names2, aw2, a2t, "a")
        for x in names1:
            res1[x]["cs"].append(max(0.0, gauss(proj1[x]["cs"] * length,
                                                proj1[x]["cs_std"] * 0.55)))
        for x in names2:
            res2[x]["cs"].append(max(0.0, gauss(proj2[x]["cs"] * length,
                                                proj2[x]["cs_std"] * 0.55)))
    return res1, res2


def _pick_line(mean, kind):
    """Half-integer prop line just under the projection (matches the DK board style)."""
    if kind == "cs":
        return round(mean / 5.0) * 5 - 0.5
    return _math.floor(mean) + 0.5


def _picks_from_samples(picks, samp, proj, team, m, method):
    """Turn a roster's simulated K/A/CS samples into More/Less Pick 6 leans."""
    for nm, pr in proj.items():
        arr = samp[nm]
        for stat, key, mean, kind in (("kills", "k", pr["kills"], "cnt"),
                                      ("assists", "a", pr["assists"], "cnt"),
                                      ("CS", "cs", pr["cs"], "cs")):
            line = _pick_line(mean, kind)
            p_over = _emp_over(arr[key], line)
            s, prob = ("More", p_over) if mean >= line else ("Less", 1 - p_over)
            if not (0.5 <= prob <= 0.9):
                continue
            picks.append({"player": nm, "team": team, "role": pr["role"],
                          "stat": stat, "line": round(line, 1), "side": s,
                          "prob": round(prob * 100, 1), "proj": mean, "method": method,
                          "matchup": f"{m['team1']} vs {m['team2']}", "league": m["league"]})


def _picks_from_poisson(picks, proj, team, m):
    """Fallback for a one-sided matchup (only one roster known): independent
    Poisson/Normal, the way the props were computed before the correlated sim."""
    for nm, pr in proj.items():
        for stat, key, mean, std, kind in (("kills", "k", pr["kills"], pr["kills_std"], "cnt"),
                                           ("assists", "a", pr["assists"], pr["assists_std"], "cnt"),
                                           ("CS", "cs", pr["cs"], pr["cs_std"], "cs")):
            line = _pick_line(mean, kind)
            p_over = _pois_over(mean, line) if kind == "cnt" else _norm_over(mean, std, line)
            s, prob = ("More", p_over) if mean >= line else ("Less", 1 - p_over)
            if not (0.5 <= prob <= 0.9):
                continue
            picks.append({"player": nm, "team": team, "role": pr["role"],
                          "stat": stat, "line": round(line, 1), "side": s,
                          "prob": round(prob * 100, 1), "proj": mean, "method": "independent",
                          "matchup": f"{m['team1']} vs {m['team2']}", "league": m["league"]})


_board_inflight = set()


def board(max_matches=6):
    """The LoL slate with rosters + per-player projections + a DK Pick 6 board.
    NON-BLOCKING: Leaguepedia's Cargo API is rate-limited, so a full slate can't be
    assembled inside one request. We return the cached board if fresh, otherwise
    kick a single paced background build (guarded so retries don't pile on) and
    return None for now -- the frontend polls until it lands. Individual team pulls
    are cached 6h, so successive builds converge fast."""
    import boardshare
    return boardshare.nonblocking(f"lol_{max_matches}", 900, _cache,
                                  ("lol_board", max_matches),
                                  lambda: _build_board(max_matches),
                                  "LOL-board-build")


def _build_board(max_matches):
    slate = upcoming(limit=max_matches)
    if not slate:
        return None
    matches, picks, seen_team = [], [], {}
    elo = elo_map()
    if True:

        def roster(team):
            if team not in seen_team:
                seen_team[team] = team_players(team)
            return seen_team[team]

        def disp(proj):                          # display roster (regressed projection)
            return [{"player": nm, "role": pr["role"], "n": pr["n"], "kills": pr["kills"],
                     "assists": pr["assists"], "cs": pr["cs"], "champs": pr["champs"]}
                    for nm, pr in proj.items()]

        for m in slate[:max_matches]:
            r1, r2 = roster(m["team1"]), roster(m["team2"])
            if not r1 and not r2:
                continue
            proj1 = {nm: player_projection(p) for nm, p in r1.items()}
            proj2 = {nm: player_projection(p) for nm, p in r2.items()}
            # Props come from the correlated per-map sim when BOTH rosters are known
            # (so kills can be zero-sum across the two teams); otherwise fall back to
            # the independent model for whichever side we have.
            if proj1 and proj2:
                pmap = _game_wp(m["team1"], m["team2"], elo)   # team1's per-map win prob
                s1, s2 = simulate_map(proj1, proj2, pmap)
                _picks_from_samples(picks, s1, proj1, m["team1"], m, "correlated sim")
                _picks_from_samples(picks, s2, proj2, m["team2"], m, "correlated sim")
            else:
                if proj1:
                    _picks_from_poisson(picks, proj1, m["team1"], m)
                if proj2:
                    _picks_from_poisson(picks, proj2, m["team2"], m)
            w1 = match_winprob(m["team1"], m["team2"], m["bo"], elo)
            matches.append({"team1": m["team1"], "team2": m["team2"], "bo": m["bo"],
                            "league": m["league"], "date": m["date"], "dt": m["dt"],
                            "win1": w1, "win2": round(100 - w1, 1),
                            "roster1": disp(proj1), "roster2": disp(proj2)})
        picks.sort(key=lambda x: -x["prob"])
        if not matches:
            return None                      # nothing loaded yet -> let it retry
        # Distinct tournaments in the slate, for the futures (title-odds) picker.
        tourns, seen_t = [], set()
        for m in slate:
            pg = m.get("tournament")
            if pg and pg not in seen_t:
                seen_t.add(pg)
                tourns.append({"page": pg, "league": m.get("league"),
                               "label": pg.replace("/", " · ")})
        return {"matches": matches, "picks": picks[:60], "payouts": _PICK6_PAYOUT,
                "tournaments": tourns[:12],
                "note": "Correlated per-map sim (kills zero-sum across the two teams). "
                        "DK/PrizePicks lines govern - match ours to their board."}
