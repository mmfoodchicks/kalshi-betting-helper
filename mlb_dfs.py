"""DraftKings MLB DFS engine driven by the game simulator.

Most DFS tools multiply flat projections and assume players are independent.
Vigil simulates each game (the same base-running sim used for props) and reads
every HITTER's DraftKings fantasy-point distribution straight off the shared
games -- so a team's hitters are correctly correlated and STACKS are valued on
their real ceiling. Pitchers are simulated from their rate stats.

On top of raw projections it adds the things the big DFS sites charge for:
  - projected OWNERSHIP + LEVERAGE (production the field is under-rostering)
  - a MULTI-LINEUP portfolio builder with exposure / uniqueness / stack rules
  - a CONTEST simulation (your lineups vs a simulated field) -> win% / ROI

...plus an edge they don't have: every player is cross-checked against Kalshi's
betting-market odds, so "SHARP leverage" = under-owned by the field AND liked by
the money. Classic roster: P,P,C,1B,2B,3B,SS,OF,OF,OF under a $50,000 cap.
"""

import math
import random
import statistics
import unicodedata

import dk_scoring

import simulate as _sim   # parse_dk_csv

ROSTER = ["P", "P", "C", "1B", "2B", "3B", "SS", "OF", "OF", "OF"]
_POS_MAP = {"SP": "P", "RP": "P", "P": "P", "C": "C", "1B": "1B", "2B": "2B",
            "3B": "3B", "SS": "SS", "OF": "OF", "LF": "OF", "CF": "OF", "RF": "OF"}


def _norm(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return " ".join("".join(c for c in s.lower() if c.isalpha() or c == " ").split())


_SUFFIX = {"jr", "sr", "ii", "iii", "iv", "v"}


def _name_parts(name):
    """(lastname, first_initial) from either a full "First Last" name (DraftKings /
    posted lineups) or the roster profile's "Last, I" form -- so the two feeds can
    be matched despite their different formats."""
    toks = [t for t in _norm(name).split() if t not in _SUFFIX]
    if not toks:
        return ("", "")
    if len(toks) >= 2 and len(toks[-1]) == 1:      # "Last I" (profile boxscore form)
        return (toks[0], toks[-1])
    if len(toks) >= 2:                              # "First Last"
        return (toks[-1], toks[0][0])
    return (toks[0], "")                            # single token (e.g. "Meidroth")


def _eligible(pos_str):
    out = set()
    for tok in (pos_str or "").replace("-", "/").split("/"):
        p = _POS_MAP.get(tok.strip().upper())
        if p:
            out.add(p)
    return out


def _primary_pos(elig):
    """Scarcest roster position a player fills (for ownership/field assignment)."""
    order = ["C", "SS", "2B", "3B", "1B", "P", "OF"]
    for pos in order:
        if pos in elig:
            return pos
    return next(iter(elig)) if elig else "OF"


def _dist(arr):
    arr = sorted(arr)
    n = len(arr)
    if not n:
        return None
    q = lambda f: arr[min(n - 1, int(f * n))]
    return {"proj": round(statistics.fmean(arr), 1), "floor": round(q(0.1), 1),
            "median": round(q(0.5), 1), "ceil": round(q(0.9), 1)}


def _pois(lam):
    if lam <= 0:
        return 0
    L = math.exp(-lam); k = 0; p = 1.0
    while True:
        k += 1; p *= random.random()
        if p <= L:
            return k - 1


def _pitcher_dk_arr(sp, team_win_prob, n=3000):
    """Simulated DraftKings point samples for a starter (list, or None)."""
    era, whip, k9 = sp.get("era"), sp.get("whip"), sp.get("k9")
    if not era or not whip or not k9:
        return None
    # Per-start innings from his REAL workload: the walk-aware est_ip (a wild arm
    # gets pulled sooner) rides along from the slate -- the same number the K props
    # use; else his expected innings; else true IP/GS; else 5.7.
    ip_total = sp.get("ip") or 0
    gs = sp.get("gs") or 0
    if sp.get("est_ip"):
        exp_ip = sp["est_ip"]
    elif sp.get("exp_ip"):
        exp_ip = sp["exp_ip"]
    elif ip_total > 0 and gs:
        exp_ip = max(3.0, min(7.2, ip_total / gs))
    elif ip_total > 0:
        starts = max(1, round(ip_total / 5.5))
        exp_ip = max(4.5, min(6.7, ip_total / starts))
    else:
        exp_ip = 5.7
    out = []
    for _ in range(n):
        ip = max(3.0, min(8.0, random.gauss(exp_ip, 1.0)))
        outs = ip * 3
        k = _pois(k9 / 9.0 * ip)
        er = _pois(era / 9.0 * ip)
        br = whip * ip
        h = _pois(br * 0.74)
        bb = _pois(br * 0.26)
        win = 4 if random.random() < (team_win_prob or 0.5) * 0.62 else 0
        out.append(outs * 0.75 + 2 * k - 2 * er - 0.6 * h - 0.6 * bb + win)
    return out


def projections(games, n_sims=4000):
    """{normalized name: {kind, dist..., team, game, arr}} for every hitter (from
    the correlated game sim) and starter (simulated line) on the slate. `arr` is
    the raw per-sim DK score sample, kept so the contest sim can draw correlated
    slate outcomes (same-game hitters share sim indices)."""
    import mlb_sim
    proj = {}
    for g in games:
        if (g.get("live") or {}).get("state") == "Final":
            continue
        sim = mlb_sim.simulate(g, n_sims)
        for side, abbr in (("home", g.get("home_abbr")), ("away", g.get("away_abbr"))):
            for name, arr in (sim["bat"][side] or {}).items():
                d = _dist(arr["dk"])
                if d:
                    proj[_norm(name)] = {"kind": "bat", "team": abbr or side,
                                         "game": g.get("game_pk"), "arr": list(arr["dk"]), **d}
        for sp, wp, abbr in ((g.get("home_sp"), g.get("p_home"), g.get("home_abbr")),
                             (g.get("away_sp"), g.get("p_away"), g.get("away_abbr"))):
            if sp and sp.get("name"):
                a = _pitcher_dk_arr(sp, wp)
                if a:
                    d = _dist(a)
                    proj[_norm(sp["name"])] = {"kind": "pit", "team": abbr or "",
                                               "game": g.get("game_pk"), "arr": a, **d}
    return proj


# ---- Deep pitch-by-pitch projections (shared engine) -----------------------
_LG_RUNS = 4.35     # league average runs per team per game (env normalizer)


def _team_ids(season):
    """{team abbreviation: MLB team id} so we can map a slate's abbrs to the deep
    roster profiles (the slate carries abbrs + starter names, not ids)."""
    import baseball

    def build():
        d = baseball._get(f"https://statsapi.mlb.com/api/v1/teams?sportId=1&season={season}")
        return {t.get("abbreviation", ""): t["id"] for t in d.get("teams", []) if t.get("abbreviation")}
    return baseball._cached(("dfs_team_ids", season), 86400, build) or {}


def _match_sp(prof, name):
    """The posted starter from the deep profile, matched by name (full, then last),
    falling back to the ace if the slate's starter isn't in the profile."""
    pool = (prof.get("rotation") or []) + (prof.get("bullpen") or []) + (prof.get("depth") or [])
    tgt = _norm(name)
    if tgt:
        for p in pool:
            if _norm(p["name"]) == tgt:
                return p
        last = tgt.split()[-1]
        for p in pool:
            if _norm(p["name"]).split()[-1:] == [last]:
                return p
    return (prof.get("rotation") or [None])[0]


def _hitter_dk(l):
    """DraftKings hitter points from a deep box line (dk_scoring.MLB_HIT).

    STOLEN BASES were being thrown away. The comment here said SB was "not
    modeled", but the engine has simulated steals since the catcher-arm work --
    the box line carries `sb` -- and DK pays +5 for one, more than a double is
    worth over a single. Discarding them cost every speed bat real projection,
    and the fastest players are exactly the cheap ones a lineup leans on.

    HBP folds into the walk path upstream and pays the same +2, so it needs no
    separate term; `.get` keeps this safe against an older line dict."""
    s = dk_scoring.MLB_HIT
    singles = l["h"] - l["2b"] - l["3b"] - l["hr"]
    return (s["single"] * singles + s["double"] * l["2b"] + s["triple"] * l["3b"]
            + s["hr"] * l["hr"] + s["rbi"] * l["rbi"] + s["run"] * l["r"]
            + s["bb"] * l["bb"] + s["hbp"] * l.get("hbp", 0)
            + s["sb"] * l.get("sb", 0))


def _pitcher_dk(l, won):
    """DraftKings pitcher points from a deep box line. Win goes to a starter whose
    side won and who went 5+ (15 outs) — DK's rule, approximated. Complete-game
    (+2.5), CG-shutout (+2.5) and no-hitter (+5) bonuses are rare, but the deep
    engine actually produces them (it rides gems deep) and they're pure ceiling —
    exactly what a GPP pitcher pick is for."""
    s = dk_scoring.MLB_PIT
    win = s["win"] if (won and l["outs"] >= 15) else 0
    pts = (s["out"] * l["outs"] + s["k"] * l["k"] + s["er"] * l["r"]
           + s["hit"] * l["h"] + s["bb"] * l["bb"] + win)
    if l["outs"] >= 27:
        pts += s["cg"]                              # complete game
        if l["r"] == 0:
            pts += s["cg_shutout"]                  # CG shutout
        if l["h"] == 0:
            pts += s["no_hitter"]                   # no-hitter
    return pts


def deep_projections(games, season, n=3000):
    """Per-player DraftKings point distributions from the DEEP pitch-by-pitch
    engine -- the same simulator behind the 4,000-season run. Each slate game is
    played with the ACTUAL posted starters, so pitchers face the real opposing
    lineup (not a standalone ERA/WHIP formula) and same-game hitters are
    correlated for stacking. Park + weather enter via a run-environment multiplier
    derived from the slate's expected runs. Same output shape as projections()."""
    import deep_data
    import deep_sim
    tid = _team_ids(season)
    proj = {}
    for g in games:
        if (g.get("live") or {}).get("state") == "Final":
            continue
        hid, aid = tid.get(g.get("home_abbr")), tid.get(g.get("away_abbr"))
        if not hid or not aid:
            continue
        try:
            hp = deep_data.team_profile(hid, season)
            ap = deep_data.team_profile(aid, season)
        except Exception:
            continue
        if not (hp and ap and hp.get("lineup") and ap.get("lineup")):
            continue
        sp_h = _match_sp(hp, (g.get("home_sp") or {}).get("name"))
        sp_a = _match_sp(ap, (g.get("away_sp") or {}).get("name"))
        sp_h_id = sp_h["id"] if sp_h else None
        sp_a_id = sp_a["id"] if sp_a else None
        er = (g.get("exp_runs_home") or 4.3) + (g.get("exp_runs_away") or 4.3)
        env = max(0.72, min(1.40, er / (2 * _LG_RUNS)))   # park + weather signal
        # DFS only rosters players who actually START. The sim still plays the
        # whole roster (bench/bullpen) for realism, but we surface ONLY the posted
        # starting nine + the two starting pitchers. When MLB has posted the real
        # lineup we filter to exactly those names; otherwise we fall back to the
        # profile's nine regulars. Relievers are never DFS-relevant on a starter
        # slate, so the only pitchers we keep are the two starters.
        gprops = g.get("props") or {}
        # The posted lineup carries FULL names ("Bobby Witt Jr."); the roster profile
        # stores abbreviated ones ("Witt, B"). Index the posted names by
        # (last, initial) so we can both (a) tell who's actually starting and
        # (b) re-key our projection to the full name DraftKings uses.
        posted_full, posted_last = {}, {}
        for _k in ("batters_home", "batters_away"):
            for b in (gprops.get(_k) or []):
                full = b.get("name") or ""
                if not full:
                    continue
                last, init = _name_parts(full)
                posted_full[(last, init)] = full
                posted_last.setdefault(last, []).append(full)
        posted = bool(posted_full)

        def _posted_name(profile_name):
            """The posted full name for a profile player, or None if not starting."""
            last, init = _name_parts(profile_name)
            if (last, init) in posted_full:
                return posted_full[(last, init)]
            cands = posted_last.get(last)
            return cands[0] if cands and len(cands) == 1 else None
        meta_bat, meta_pit = {}, {}
        start_bat_ids = set()
        for prof, abbr in ((hp, g.get("home_abbr")), (ap, g.get("away_abbr"))):
            for b in (prof.get("lineup") or []) + (prof.get("bench") or []):
                meta_bat[b["id"]] = (b["name"], abbr)
            for b in (prof.get("lineup") or [])[:9]:    # profile's assumed regulars
                start_bat_ids.add(b["id"])
            for p in (prof.get("rotation") or []) + (prof.get("bullpen") or []):
                meta_pit[p["id"]] = (p["name"], abbr)
        start_pit_ids = {pid for pid in (sp_h_id, sp_a_id) if pid}

        sp_fullname = {sp_h_id: (g.get("home_sp") or {}).get("name"),
                       sp_a_id: (g.get("away_sp") or {}).get("name")}

        def _is_starter_bat(pid):
            nm, _ = meta_bat.get(pid, (None, None))
            if not nm:
                return False
            if posted:                                  # real lineup posted -> trust it
                return _posted_name(nm) is not None
            return pid in start_bat_ids                 # else fall back to the regulars
        bat_acc, pit_acc = {}, {}
        for i in range(n):
            res = deep_sim.play_game(hp, ap, sp_h, sp_a, env=env)
            hw = res["home_win"]
            for pid, line in res["batting"].items():
                bat_acc.setdefault(pid, [0.0] * n)[i] = _hitter_dk(line)
            for pid, line in res["pitching"].items():
                won = (hw and pid == sp_h_id) or ((not hw) and pid == sp_a_id)
                pit_acc.setdefault(pid, [0.0] * n)[i] = _pitcher_dk(line, won)
        # Pitchers first so a two-way player's HITTER line wins the name key (the
        # common DFS case). Only the two starting pitchers are surfaced.
        for pid, arr in pit_acc.items():
            if pid not in start_pit_ids:
                continue
            _pnm, abbr = meta_pit.get(pid, (None, None))
            # Key by the slate's full starter name (DraftKings form), not the
            # profile's abbreviated one, so the CSV actually matches.
            nm = sp_fullname.get(pid) or _pnm
            d = _dist(arr) if nm else None
            if d and d["proj"] > 1:
                proj[_norm(nm)] = {"kind": "pit", "team": abbr or "",
                                   "game": g.get("game_pk"), "arr": arr, **d}
        # Stolen bases: the deep engine doesn't model steals, but DK pays +5 per
        # SB — a full point-plus per game for the true burners (Witt/De La Cruz
        # class). Add each hitter's expected SB points from his season steal rate
        # (props carries sbr = attempts per time on first; ~1.1 successful-steal
        # opportunities convert per game of on-base traffic).
        sb_bonus = {}
        for side in ("batters_home", "batters_away"):
            for bp in ((g.get("props") or {}).get(side) or []):
                sbr = bp.get("sbr") or 0.0
                if sbr > 0.02:
                    sb_bonus[_norm(bp.get("name") or "")] = 5.0 * min(0.55, sbr * 1.1)
        for pid, arr in bat_acc.items():
            if not _is_starter_bat(pid):
                continue
            _pnm, abbr = meta_bat.get(pid, (None, None))
            # Prefer the posted full name; fall back to the profile name pre-lineup.
            nm = (_posted_name(_pnm) if posted else None) or _pnm
            if not nm:
                continue
            sb = sb_bonus.get(_norm(nm), 0.0)
            if sb:
                arr = [x + sb for x in arr]     # mean AND quantiles shift together
            d = _dist(arr)
            if d:
                proj[_norm(nm)] = {"kind": "bat", "team": abbr or "",
                                   "game": g.get("game_pk"), "arr": arr, **d}
    return proj


# ---- Simulated batter-vs-starting-pitcher (from the deep engine) -----------
import threading as _thr
import time as _tm
_bvp_inflight = set()


def _compute_game_bvp(g, season, n):
    """Run the deep pitch-by-pitch engine n times for one game and aggregate every
    batter's line against the opposing STARTER -> {batter_id: {ab,h,hr,k,bb,pa,
    avg,k_pct,hr_pct,bb_pct}}. batter_id is the real MLB id (matches the live box)."""
    import deep_data
    import deep_sim
    tid = _team_ids(season)
    hid, aid = tid.get(g.get("home_abbr")), tid.get(g.get("away_abbr"))
    if not hid or not aid:
        return {}
    try:
        hp = deep_data.team_profile(hid, season)
        ap = deep_data.team_profile(aid, season)
    except Exception:
        return {}
    if not (hp and ap and hp.get("lineup") and ap.get("lineup")):
        return {}
    sp_h = _match_sp(hp, (g.get("home_sp") or {}).get("name"))
    sp_a = _match_sp(ap, (g.get("away_sp") or {}).get("name"))
    er = (g.get("exp_runs_home") or 4.3) + (g.get("exp_runs_away") or 4.3)
    env = max(0.72, min(1.40, er / (2 * _LG_RUNS)))
    acc = {}
    for _ in range(n):
        deep_sim.play_game(hp, ap, sp_h, sp_a, env=env, bvp=acc)
    out = {}
    for pid, l in acc.items():
        pa, ab = l["pa"], l["ab"]
        if pa < 1:
            continue
        out[pid] = {"pa": pa, "ab": ab, "h": l["h"], "hr": l["hr"], "k": l["k"], "bb": l["bb"],
                    "avg": round(l["h"] / ab, 3) if ab else 0.0,
                    "k_pct": round(100 * l["k"] / pa, 1),
                    "hr_pct": round(100 * l["hr"] / pa, 1),
                    "bb_pct": round(100 * l["bb"] / pa, 1)}
    return out


def game_sim_bvp(g, season, n=600):
    """Simulated batter-vs-starter lines for one game. NON-BLOCKING: returns the
    cached result if fresh, else kicks a background compute and returns {} for now
    (it lands on a later call). Cached 6h -- the matchup doesn't change intra-day."""
    import racing
    pk = g.get("game_pk")
    if not pk:
        return {}
    key = ("sim_bvp", pk)
    hit = racing._form_cache.get(key)
    if hit and (_tm.time() - hit[0]) < 6 * 3600 and hit[1] is not None:
        return hit[1]
    if pk not in _bvp_inflight:
        _bvp_inflight.add(pk)

        def _bg():
            try:
                racing._cached(key, 6 * 3600, lambda: _compute_game_bvp(g, str(season), n))
            finally:
                _bvp_inflight.discard(pk)
        _thr.Thread(target=_bg, daemon=True).start()
    return {}


# ---- Kalshi betting-market signals (the edge the DFS sites don't have) -----
def market_signals(season):
    """{normalized name: market-implied boom probability} from Kalshi's batter
    prop prices (1+/2+ hits, 1+ HR). This is an independent, money-backed read on
    a hitter's upside that no DFS projection site has."""
    try:
        import value
    except Exception:
        return {}
    out = {}
    for series, stat in (("KXMLBHIT", "hits"), ("KXMLBHR", "hr")):
        for m in value._markets(series):
            mt = value._TITLE.match(m.get("title", "") or "")
            if not mt:
                continue
            import kalshi
            nm = value._norm(mt.group(1).strip())
            line = int(mt.group(2))
            yc = kalshi._cents(m.get("yes_ask_dollars"))
            if yc is None or yc <= 1 or yc >= 99:
                continue
            d = out.setdefault(nm, {})
            d[(stat, line)] = yc / 100.0
    # Collapse to a single boom probability: P(2+ hits OR 1+ HR), independent-ish.
    boom = {}
    for nm, d in out.items():
        p_hit2 = d.get(("hits", 2))
        p_hr1 = d.get(("hr", 1))
        if p_hit2 is None and p_hr1 is None:
            continue
        p = 1.0
        if p_hit2 is not None:
            p *= (1 - p_hit2)
        if p_hr1 is not None:
            p *= (1 - p_hr1)
        boom[nm] = round((1 - p) * 100, 1)
    return boom


# ---- Ownership + leverage --------------------------------------------------
def _appeal(p):
    """Raw roster appeal: points per $1k salary (what drives ownership)."""
    sal = max(2000, p["salary"])
    return p["median"] / (sal / 1000.0)


def add_ownership_leverage(players, market_boom):
    """Annotate each player with projected own%, leverage, and sharp leverage.

    Ownership: within each roster position the field's rosters must fill `slots`
    spots, so ownership across that position sums to slots*100%. We split that by
    appeal^k (chalk gets the lion's share), capped per player.

    Leverage      = production percentile - ownership percentile (under-rostered
                    production = good GPP play).
    Sharp leverage = market-implied boom% - ownership% (the money likes him more
                    than the field does -- Vigil's edge).
    """
    if not players:
        return
    # Median percentile across the whole slate (for leverage).
    meds = sorted(p["median"] for p in players)
    n = len(meds)
    def pct_rank(v):
        lo = sum(1 for m in meds if m < v)
        return 100.0 * lo / max(1, n - 1)

    # Ownership per position group.
    groups = {}
    for p in players:
        groups.setdefault(_primary_pos(p["elig"]), []).append(p)
    for pos, grp in groups.items():
        slots = ROSTER.count(pos) or 1
        budget = slots * 100.0
        weights = [max(0.01, _appeal(p)) ** 2.4 for p in grp]
        tot = sum(weights) or 1.0
        for p, w in zip(grp, weights):
            own = budget * w / tot
            p["own"] = round(min(75.0, own), 1)

    own_vals = sorted(p.get("own", 0.0) for p in players)
    def own_pct_rank(v):
        lo = sum(1 for o in own_vals if o < v)
        return 100.0 * lo / max(1, n - 1)

    for p in players:
        p.setdefault("own", 0.0)
        p["lev"] = round(pct_rank(p["median"]) - own_pct_rank(p["own"]), 1)
        boom = market_boom.get(_norm(p["name"]))
        p["mkt_boom"] = boom
        p["sharp"] = round(boom - p["own"], 1) if boom is not None else None


# ---- Optimizer (single lineup) ---------------------------------------------
def _value(p, objective):
    return p["ceil"] if objective == "ceiling" else p["median"]


def _build_one(by_pos, cap, objective, stack_team=None, stack_min=0, rng=random):
    """One greedy-randomized valid lineup. If a stack is requested, hitter slots
    are seeded from that team first so the lineup actually stacks."""
    used, lineup, sal = set(), [], 0
    slots = ROSTER
    order = sorted(range(len(slots)), key=lambda i: len(by_pos[slots[i]]))
    # Seed the stack: take the cheapest-by-value path by trying stack-team hitters
    # for the earliest hitter slots.
    stacked = 0
    for si in order:
        pos = slots[si]
        pool = [p for p in by_pos[pos] if p["name"] not in used and sal + p["salary"] <= cap]
        if not pool:
            return None
        cand = pool
        if stack_team and pos != "P" and stacked < stack_min:
            team_pool = [p for p in pool if p.get("team") == stack_team]
            if team_pool:
                cand = team_pool
        top = cand[:12]
        weights = [max(0.1, _value(x, objective)) ** 2 for x in top]
        pick = rng.choices(top, weights=weights)[0]
        if stack_team and pick.get("team") == stack_team and pos != "P":
            stacked += 1
        used.add(pick["name"]); lineup.append(pick); sal += pick["salary"]
    if len(lineup) != len(slots) or sal > cap:
        return None
    if stack_team and stacked < stack_min:
        return None
    return lineup, sal


def optimize(players, cap, objective, restarts=6000):
    by_pos = _by_pos(players)
    if by_pos is None:
        return None
    best = None
    for _ in range(restarts):
        r = _build_one(by_pos, cap, objective)
        if not r:
            continue
        lineup, sal = r
        score = sum(_value(p, objective) for p in lineup)
        if best is None or score > best[0]:
            best = (score, lineup, sal)
    return best


def _by_pos(players):
    by_pos = {pos: [] for pos in set(ROSTER)}
    for p in players:
        for pos in p["elig"]:
            if pos in by_pos:
                by_pos[pos].append(p)
    for pos in by_pos:
        by_pos[pos].sort(key=lambda p: -p["median"])
    if any(len(by_pos[pos]) < ROSTER.count(pos) for pos in set(ROSTER)):
        return None
    return by_pos


# ---- Multi-lineup portfolio ------------------------------------------------
def _lineup_key(lineup):
    return frozenset(p["name"] for p in lineup)


def optimize_portfolio(players, cap, objective, n_lineups=20,
                       max_exposure=60.0, min_uniq=2, stack_min=4):
    """Build a diversified set of lineups with exposure caps, a uniqueness floor,
    and team stacking -- what GPP mass-multi-entry actually needs."""
    by_pos = _by_pos(players)
    if by_pos is None:
        return None
    teams = sorted({p.get("team") for p in players if p.get("team")})
    max_count = max(1, math.ceil(max_exposure / 100.0 * n_lineups))

    # Large candidate pool: a mix of stacked builds (rotating the stack team) and
    # free builds, so we have variety to select a diversified portfolio from.
    enforce_stack = stack_min >= 2 and bool(teams)
    pool, seen = [], set()
    target = max(400, n_lineups * 40)
    attempts = 0
    while len(pool) < target and attempts < target * 5:
        attempts += 1
        # When stacking, always seed a (rotating) stack team; otherwise free build.
        st = random.choice(teams) if enforce_stack else None
        r = _build_one(by_pos, cap, objective, stack_team=st, stack_min=stack_min if st else 0)
        if not r:
            continue
        lineup, sal = r
        key = _lineup_key(lineup)
        if key in seen:
            continue
        seen.add(key)
        score = sum(_value(p, objective) for p in lineup)
        pool.append((score, lineup, sal))
    pool.sort(key=lambda x: -x[0])

    chosen, exposure = [], {}
    for score, lineup, sal in pool:
        if len(chosen) >= n_lineups:
            break
        # Stack rule: every lineup must hit the requested stack size.
        if enforce_stack and (_biggest_stack(lineup) or {}).get("n", 0) < stack_min:
            continue
        key = _lineup_key(lineup)
        # Uniqueness: differ from every chosen lineup by >= min_uniq players.
        if any(len(key & _lineup_key(c[1])) > len(ROSTER) - min_uniq for c in chosen):
            continue
        # Exposure cap: no player may exceed max_count across the set.
        if any(exposure.get(p["name"], 0) + 1 > max_count for p in lineup):
            continue
        chosen.append((score, lineup, sal))
        for p in lineup:
            exposure[p["name"]] = exposure.get(p["name"], 0) + 1
    return chosen


# ---- Contest simulation (your lineups vs a simulated field) ----------------
def _field_lineup(by_pos, cap, own_weights, rng, tries=6):
    """One ownership-weighted valid field lineup (list of names) or None."""
    for _ in range(tries):
        used, names, sal = set(), [], 0
        ok = True
        for pos in sorted(ROSTER, key=lambda p: len(by_pos[p])):
            pool = [p for p in by_pos[pos] if p["name"] not in used and sal + p["salary"] <= cap]
            if not pool:
                ok = False
                break
            w = [own_weights.get(p["name"], 1.0) for p in pool]
            pick = rng.choices(pool, weights=w)[0]
            used.add(pick["name"]); names.append(pick["name"]); sal += pick["salary"]
        if ok and len(names) == len(ROSTER):
            return names
    return None


def _ncdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _npdf(z):
    return math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


def _gpp_curve(C, pool, first, fee):
    """Analytic top-heavy GPP payout(rank) for a C-entry contest.

    Pays ~20% of the field; 1st = `first`, decaying as a power law down to a
    ~1.5x-entry min-cash, with the exponent solved so the paid spots sum to about
    `pool`. Returns (payout_fn, places_paid). This is what lets us price a real
    11,000- or 1,000,000-entry tournament without simulating every entrant."""
    places = max(1, int(round(0.20 * C)))
    min_cash = max(fee * 1.5, 0.01)
    first = max(first, min_cash)

    def total_for(k):
        if k <= 0:
            rstar = float(places)
        else:
            rstar = min(float(places), max(1.0, (first / min_cash) ** (1.0 / k)))
        if abs(k - 1.0) < 1e-6:
            integ = first * math.log(max(1e-9, rstar))
        else:
            integ = first * (rstar ** (1.0 - k) - 1.0) / (1.0 - k)
        return integ + max(0.0, places - rstar) * min_cash

    lo, hi = 0.2, 4.0                                  # bisection on the exponent
    for _ in range(40):
        k = 0.5 * (lo + hi)
        if total_for(k) > pool:
            lo = k                                     # steeper decay -> smaller total
        else:
            hi = k
    k = 0.5 * (lo + hi)

    def payout(r):
        if r < 1 or r > places:
            return 0.0
        return max(min_cash, first * r ** (-k))
    return payout, places


def _rank_grid(places, n=240):
    """Log-spaced (rank, width) cells over the paid ranks 2..places, for
    integrating the steep payout curve against the finishing-rank distribution."""
    if places < 2:
        return []
    lo, hi = math.log(2.0), math.log(float(places))
    if hi <= lo:
        return [(2.0, 1.0)]
    edges = [math.exp(lo + (hi - lo) * i / n) for i in range(n + 1)]
    return [((edges[i] + edges[i + 1]) / 2.0, edges[i + 1] - edges[i]) for i in range(n)]


def contest_sim(your_lineups, players, sample_size=600, n_iter=500, contest="gpp",
                entry_fee=1.0, contest_size=None, prize_pool=None, first_prize=None):
    """Estimate win% / cash% / ROI for your lineups in a contest of ANY size.

    Big GPPs have tens of thousands to millions of entries; you can't simulate them
    all. Instead we build a fast ownership-weighted SAMPLE field, and on each slate
    outcome fit the opponents' score distribution. From that we get q = the chance a
    random opponent beats your lineup this slate, then extrapolate analytically to
    the real `contest_size`: you win only if all C-1 opponents fall below you
    (prob (1-q)^(C-1)), your finishing rank ~ 1 + Binomial(C-1, q), and we integrate
    a realistic top-heavy payout curve over that rank distribution for ROI.

    Honest: the field model, the normal score-fit and the payout curve are
    approximations -- but they scale correctly with contest size, which a literal
    few-hundred-entry sim never did."""
    by_pos = _by_pos(players)
    if by_pos is None or not your_lineups:
        return None
    arr = {p["name"]: p.get("arr") for p in players if p.get("arr")}
    if not arr:
        return None
    L = min(len(a) for a in arr.values())
    own_w = {p["name"]: max(0.1, p.get("own", 1.0)) for p in players}

    rng = random.Random(12345)
    # A real GPP field mostly optimizes against the same public projections, so
    # its lineups cluster near the optimum. Keep sampled field lineups projecting
    # >= ~84% of YOUR best build — an unfiltered random field is far too soft and
    # prints fantasy win%/ROI numbers.
    proj_of = {p["name"]: (p.get("proj") or 0.0) for p in players}
    ref = max(sum(proj_of.get(p["name"], 0.0) for p in ln) for ln in your_lineups)
    floor_q = 0.84 * ref
    field, attempts = [], 0
    while len(field) < sample_size and attempts < sample_size * 40:
        attempts += 1
        fl = _field_lineup(by_pos, 50000, own_w, rng)
        if fl and sum(proj_of.get(nm, 0.0) for nm in fl) >= floor_q:
            field.append(fl)
    if len(field) < 30:                     # thin slate -> relax the sharpness floor
        for _ in range(sample_size * 5):
            fl = _field_lineup(by_pos, 50000, own_w, rng)
            if fl:
                field.append(fl)
            if len(field) >= sample_size:
                break
    if len(field) < 30:
        return None

    C = max(2, int(contest_size or (len(field) + 1)))
    pool = float(prize_pool) if prize_pool else entry_fee * C * 0.85   # ~15% rake
    if contest == "double_up":
        cash_places = max(1, int(round(0.45 * C)))
        du_each = pool / cash_places                  # even split among cashers (~2x)
        payout, places = (lambda r: du_each if r <= cash_places else 0.0), cash_places
        first = du_each
    else:
        first = float(first_prize) if first_prize else 0.20 * pool
        payout, places = _gpp_curve(C, pool, first, entry_fee)
    grid = _rank_grid(places)

    def score(names, it):
        s = 0.0
        for nm in names:
            a = arr.get(nm)
            s += a[it % L] if a else 0.0
        return s

    your_names = [[p["name"] for p in ln] for ln in your_lineups]
    stats = [{"win": 0.0, "top1": 0.0, "cash": 0.0, "ret": 0.0} for _ in your_names]
    top1_line = max(1, int(0.01 * C))

    for it in range(n_iter):
        fs = [score(f, it) for f in field]
        mu = statistics.fmean(fs)
        sd = statistics.pstdev(fs) or 1.0
        for li, names in enumerate(your_names):
            ys = score(names, it)
            q = max(1e-12, min(1.0, 1.0 - _ncdf((ys - mu) / sd)))   # P(an opp beats you)
            st = stats[li]
            # Win = every one of the C-1 opponents finishes below you.
            winp = math.exp((C - 1) * math.log(1.0 - q)) if q < 1.0 else 0.0
            st["win"] += winp
            # Finishing rank ~ 1 + Binomial(C-1, q), approximated Normal for large C.
            mr = 1.0 + (C - 1) * q
            sr = math.sqrt(max(1e-9, (C - 1) * q * (1.0 - q)))
            st["cash"] += _ncdf((places - mr) / sr)
            st["top1"] += _ncdf((top1_line - mr) / sr)
            if contest == "double_up":
                st["ret"] += du_each * _ncdf((places - mr) / sr)
            else:
                ev = first * winp                      # 1st exactly (rare; exact tail)
                for r, wd in grid:
                    ev += payout(r) * _npdf((r - mr) / sr) / sr * wd
                st["ret"] += ev

    out = []
    for st in stats:
        ret = st["ret"] / n_iter
        roi = 100.0 * (ret - entry_fee) / entry_fee
        out.append({"win_pct": round(100 * st["win"] / n_iter, 4),
                    "top1_pct": round(100 * st["top1"] / n_iter, 2),
                    "cash_pct": round(100 * st["cash"] / n_iter, 1),
                    "roi_pct": round(roi, 1),
                    "avg_return": round(ret, 2)})
    best = max(range(len(out)), key=lambda i: out[i]["roi_pct"]) if out else None
    return {"sample_size": len(field), "entries": C, "iterations": n_iter,
            "contest": contest, "entry_fee": entry_fee, "prize_pool": round(pool),
            "first_prize": round(first), "places_paid": places, "lineups": out,
            "best_lineup_index": best}


# ---- Public build ----------------------------------------------------------
def _lineup_payload(lineup, sal, cap, objective):
    ls = sorted(lineup, key=lambda p: ROSTER.index(_primary_pos(p["elig"])))
    return {
        "players": [{"name": p["name"], "salary": int(p["salary"]),
                     "pos": "/".join(sorted(p["elig"])), "team": p.get("team"),
                     "proj": round(p["proj"], 1), "floor": round(p["floor"], 1),
                     "ceil": round(p["ceil"], 1), "own": p.get("own"),
                     "lev": p.get("lev"), "mkt_boom": p.get("mkt_boom"),
                     "sharp": p.get("sharp"), "sim": p["sim"], "confirmed": p.get("confirmed")}
                    for p in ls],
        "salary": int(sal), "cap": cap,
        "proj": round(sum(p["proj"] for p in lineup), 1),
        "ceil": round(sum(p["ceil"] for p in lineup), 1),
        "floor": round(sum(p["floor"] for p in lineup), 1),
        "own_sum": round(sum(p.get("own", 0) for p in lineup), 1),
        "stack": _biggest_stack(lineup),
    }


def _biggest_stack(lineup):
    teams = {}
    for p in lineup:
        if p["sim"] and p.get("kind") != "pit" and p.get("team"):
            teams[p["team"]] = teams.get(p["team"], 0) + 1
    if not teams:
        return None
    tm = max(teams, key=teams.get)
    return {"team": tm, "n": teams[tm]} if teams[tm] >= 2 else None


def _assemble_pool(players_raw, proj, include_unconfirmed):
    """CSV rows -> roster pool. We prefer players the sim confirms as starting;
    `include_unconfirmed` pads the rest off DraftKings' season average so a lineup
    can still be built when lineups aren't fully posted yet."""
    players, unmatched = [], []
    for p in players_raw:
        elig = _eligible(p.get("pos"))
        if not elig:
            continue
        pr = proj.get(_norm(p["name"]))
        confirmed = pr is not None
        if not pr:
            if not include_unconfirmed or not p.get("proj"):
                unmatched.append(p["name"])
                continue
            pr = {"proj": p["proj"], "median": p["proj"], "floor": p["proj"] * 0.5,
                  "ceil": p["proj"] * 1.6, "kind": "?", "team": None, "arr": None}
        players.append({"name": p["name"], "salary": p["salary"], "elig": elig,
                        "median": pr["median"], "ceil": pr["ceil"], "floor": pr["floor"],
                        "proj": pr["proj"], "team": pr.get("team"), "kind": pr.get("kind"),
                        "arr": pr.get("arr"), "confirmed": confirmed,
                        "sim": pr.get("kind") in ("bat", "pit")})
    return players, unmatched


def _missing_positions(players):
    """Roster slots the pool can't even nominally fill (ignores the cap): a position
    whose eligible-player count is short of how many of that slot the roster needs."""
    from collections import Counter
    need = Counter(ROSTER)
    have = Counter()
    for pos in need:
        have[pos] = sum(1 for p in players if pos in p["elig"])
    return [f"{need[pos] - have[pos]}×{pos}" for pos in need if have[pos] < need[pos]]


def build(date, csv_text, cap=50000, objective="median", n_sims=4000,
          n_lineups=1, max_exposure=60.0, min_uniq=2, stack_min=4,
          contest=None, field_size=200, contest_iters=400, entry_fee=1.0,
          include_unconfirmed=False, contest_size=None, prize_pool=None,
          first_prize=None):
    import baseball
    players_raw = _sim.parse_dk_csv(csv_text)
    if len(players_raw) < 10:
        return {"error": f"need a full MLB slate CSV (got {len(players_raw)} players)"}
    try:
        games = baseball.analyze_slate(date, date[:4])
    except Exception as e:
        return {"error": f"slate failed: {e}"}
    # Deep pitch-by-pitch engine (pitchers vs the real lineup); fall back to the
    # lighter game sim if team profiles aren't available.
    season = date[:4]
    proj = {}
    try:
        proj = deep_projections(games, season, min(3000, n_sims))
    except Exception:
        proj = {}
    engine = "deep"
    if not proj:
        proj = projections(games, n_sims)
        engine = "fast"
    if not proj:
        return {"error": "no projections - lineups may not be posted yet"}
    market_boom = {}
    try:
        market_boom = market_signals(date[:4])
    except Exception:
        pass

    n_lineups = max(1, min(150, n_lineups))

    def _build_lineups(players):
        if n_lineups == 1:
            res = optimize(players, cap, objective)
            return [res] if res else None
        return optimize_portfolio(players, cap, objective, n_lineups,
                                  max_exposure, min_uniq, stack_min)

    players, unmatched = _assemble_pool(players_raw, proj, include_unconfirmed)
    lineups = _build_lineups(players)
    auto_padded = False
    # If the sim-confirmed pool alone can't field a roster (common when lineups
    # aren't fully posted yet), fall back to padding the gaps from the CSV's
    # season averages so the user still gets a lineup -- flagged as unconfirmed.
    if not lineups and not include_unconfirmed:
        players, unmatched = _assemble_pool(players_raw, proj, True)
        lineups = _build_lineups(players)
        auto_padded = bool(lineups)

    if not lineups:
        n_conf = sum(1 for p in players if p.get("confirmed"))
        miss = _missing_positions(players)
        why = (f"short {', '.join(miss)} - not enough players at those slots"
               if miss else "no combination fits the salary cap")
        return {"error": f"couldn't fill a valid roster ({why}). "
                f"Matched {n_conf} confirmed starters of {len(players_raw)} CSV players - "
                "if lineups aren't posted yet, try again closer to first pitch."}

    add_ownership_leverage(players, market_boom)

    # Slate-wide leverage board (the unique selling point): best sharp + raw plays.
    sim_players = [p for p in players if p["sim"]]
    board = sorted(sim_players, key=lambda p: (p.get("sharp") if p.get("sharp") is not None else -999, p.get("lev", -999)), reverse=True)
    leverage_board = [{"name": p["name"], "team": p.get("team"), "salary": int(p["salary"]),
                       "proj": round(p["proj"], 1), "own": p.get("own"), "lev": p.get("lev"),
                       "mkt_boom": p.get("mkt_boom"), "sharp": p.get("sharp")}
                      for p in board[:12]]

    payloads = [_lineup_payload(ln, sal, cap, objective) for _, ln, sal in lineups]

    # Exposure report across the portfolio.
    exp = {}
    for _, ln, _s in lineups:
        for p in ln:
            exp[p["name"]] = exp.get(p["name"], 0) + 1
    exposure = sorted(({"name": nm, "lineups": c,
                        "pct": round(100 * c / len(lineups), 1)}
                       for nm, c in exp.items()), key=lambda x: -x["lineups"])[:25]

    out = {
        "objective": objective, "n_lineups": len(payloads),
        "engine": engine,
        "lineups": payloads, "exposure": exposure,
        "leverage_board": leverage_board,
        "pool": len(players), "sim_players": len(sim_players),
        "unmatched": unmatched[:15],
        "auto_padded": auto_padded,
        # Back-compat single-lineup fields (older UI path reads these).
        "lineup": payloads[0]["players"], "total_salary": payloads[0]["salary"],
        "cap": cap, "total_proj": payloads[0]["proj"],
        "total_ceil": payloads[0]["ceil"], "total_floor": payloads[0]["floor"],
    }
    if contest:
        try:
            out["contest_sim"] = contest_sim(
                [ln for _, ln, _s in lineups], players,
                sample_size=min(1200, max(200, field_size)),
                n_iter=min(800, contest_iters), contest=contest, entry_fee=entry_fee,
                contest_size=contest_size, prize_pool=prize_pool, first_prize=first_prize)
        except Exception as e:
            out["contest_sim"] = {"error": f"contest sim failed: {e}"}
    return out
