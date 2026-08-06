"""DraftKings NFL Classic DFS: optimizer + correlated contest sim.

Roster: 1 QB, 2 RB, 3 WR, 1 TE, 1 FLEX (RB/WR/TE), 1 DST -- $50,000 cap.

Projections + per-player score arrays come from the Sleeper-seeded correlated
game sim (nfl_dfs_sim.player_pool): skill players carry the within-game
correlation (a QB and his WRs boom together), DSTs are sampled independently.
Salaries + the actual player pool come from the pasted DKSalaries.csv. Ceiling /
leverage objectives and the top-heavy contest sim mirror the MLB DFS builder;
the pure payout-curve helpers are reused straight from mlb_dfs.
"""

import math
import random

import simulate                     # parse_dk_csv
import nfl_dfs_sim
from mlb_dfs import _gpp_curve, _rank_grid, _ncdf, _npdf   # roster-agnostic helpers

ROSTER = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"]
FLEX_OK = {"RB", "WR", "TE"}
CAP = 50000

# --- Showdown Captain Mode ---------------------------------------------------
# A one-game slate has its own roster: 1 CPT + 5 FLEX, any position, same $50,000
# cap. The captain scores 1.5x and costs 1.5x, and DK's export lists every player
# TWICE -- once with Roster Position CPT at the higher salary, once as FLEX.
#
# Running classic rules against a showdown export is what produced "no valid
# lineup under the cap", and it could not have produced anything else: classic
# needs nine players, and on tonight's Panthers-Cardinals export DK flat-priced
# every FLEX at $7,600, so nine of them is $68,400 against a $50,000 cap. The
# showdown roster fits with room to spare -- 11,400 + 5 x 7,600 = $49,400.
SHOWDOWN_ROSTER = ["CPT", "FLEX", "FLEX", "FLEX", "FLEX", "FLEX"]
CPT_MULT = 1.5
# Kickers exist in showdown and not in classic, which is why _elig refuses them.
SHOWDOWN_POS = {"QB", "RB", "WR", "TE", "K", "DST"}
# DK requires a showdown roster to span both teams -- you cannot enter six
# players from one side.
SHOWDOWN_MIN_TEAMS = 2
# DK's own "this player is not playing" flags. Q (questionable) is deliberately
# NOT here: questionable players play all the time and pricing them out would
# throw away real leverage.
_OUT_STATUS = {"OUT", "IR", "NA", "SUSP"}


def _elig(pos):
    pos = "RB" if pos == "FB" else (pos or "").upper()
    slots = [pos] if pos in ("QB", "RB", "WR", "TE", "DST") else []
    if pos in FLEX_OK:
        slots.append("FLEX")
    return slots


def _value(p, objective):
    if objective == "ceiling":
        return p["ceiling"]
    if objective == "leverage":
        return p["ceiling"] * (1.0 - 0.007 * p.get("own", 8.0))
    return p["proj"]


def detect_mode(csv_players):
    """'showdown' or 'classic', read off the export itself.

    A showdown export is the one that carries CPT rows. Falling back to the game
    count is not enough on its own -- a one-game CLASSIC slate exists (a Monday
    nighter posted as a single-game classic contest) -- so the roster position is
    what decides, and a single game is only the tie-breaker when DK omitted it.
    """
    rps = {(c.get("roster_pos") or "").upper() for c in csv_players}
    if "CPT" in rps:
        return "showdown"
    games = {(c.get("game") or "").split(" ")[0] for c in csv_players if c.get("game")}
    if len(games) == 1 and rps and rps <= {"FLEX", "UTIL", ""}:
        return "showdown"
    return "classic"


def _playable(c):
    """Drop players DK has already ruled out. Rostering an OUT or IR player is
    never right, and the export says so plainly."""
    return (c.get("status") or "").upper() not in _OUT_STATUS


def _norm_index(pool):
    """{normalised name: entry} for the projection pool.

    Sleeper builds its name as first + last, which drops the suffix; DK keeps it
    and punctuates initials. So "Marvin Harrison Jr." never equalled "Marvin
    Harrison", "A.J. Dillon" never equalled "AJ Dillon", and roughly a third of a
    slate missed on an exact-string compare -- falling back to DK's
    AvgPointsPerGame, which is a REGULAR-SEASON number and the single thing the
    preseason model exists to override. It made a starter who will play one
    series the highest projection on the board.

    nfl_adp._norm already strips accents, suffixes, punctuation and case for
    exactly this reason; it simply was not being used here."""
    try:
        import nfl_adp
        norm = nfl_adp._norm
    except Exception:
        return {}, None
    idx = {}
    for k, v in pool.items():
        nk = norm(k)
        # First writer wins: a real player must never be displaced by a defense
        # that happens to normalise alike.
        if nk and nk not in idx:
            idx[nk] = v
        # Punctuated initials survive _norm as separate tokens -- "A.J. Dillon"
        # becomes "a j dillon" while Sleeper's "AJ Dillon" becomes "aj dillon",
        # so the two still miss. Squeezing the spaces out makes them the same
        # key. Kept as a SECOND key rather than replacing the first, so the
        # looser form is only reached when the ordinary one finds nothing.
        sk = nk.replace(" ", "")
        if sk and sk not in idx:
            idx[sk] = v
    return idx, norm


def _pool_match(pool, name, pos, team, nidx=None, norm=None):
    """This player's simulated projection, or None.

    The team-abbreviation lookup exists for ONE reason: DK writes a defense as
    "Panthers" or "CAR" and the pool registers it under both. It was applied to
    every player, so anyone whose NAME was not in the pool silently inherited his
    team's DEFENSE -- projection, ceiling, floor and sample array alike.

    On a single preseason board that was 26 players. Feleipe Franks, a fifth-
    string tight end, was not being projected as a tight end at all; his
    projection WAS the Carolina defense. And because they all took the same array
    OBJECT, a lineup holding four of them looked like it had enormous upside --
    they boomed in perfect lockstep, since they were the same numbers. That is
    the tail the ceiling objective was buying.

    Nothing warned, either: `unmatched` stayed empty because a match had been
    found. A player the model deliberately left out of the pool (beyond the
    measured exhibition depth at his position) must come back as unmatched and be
    priced as the fringe player he is."""
    hit = pool.get(name)
    if hit is not None:
        return hit
    if nidx and norm:
        nk = norm(name)
        want = (pos or "").upper()
        for key in (nk, nk.replace(" ", "")):
            hit = nidx.get(key)
            # Only across the same position -- normalising must not turn a
            # missing tight end into somebody who shares a stripped name.
            if hit is not None and (hit.get("pos") or "").upper() == want:
                return hit
    if (pos or "").upper() == "DST" and team:
        hit = pool.get(team)
        if hit is not None and hit.get("pos") == "DST":
            return hit
    return None


def _deep_fallback(pool, preseason):
    """{pos: projection} for a preseason player the depth model left out.

    Being excluded must not be an UPGRADE, and it was. A matched player carries a
    preseason projection -- two or three points, because exhibition usage is
    small. An unmatched one fell back to DK's AvgPointsPerGame, a REGULAR-SEASON
    average worth seven to ten. So every player the model deliberately judged too
    deep to see the field came back rated three times higher than the players it
    had modelled, and one of them turned up as the captain.

    A player beyond the measured exhibition depth at his position cannot
    reasonably be projected above the LAST player who made it in, so that is what
    he gets. Regular season keeps DK's average, where it means what it says."""
    if not preseason:
        return {}
    by = {}
    for v in pool.values():
        p = (v.get("pos") or "").upper()
        if p in ("QB", "RB", "WR", "TE") and v.get("proj") is not None:
            by.setdefault(p, []).append(float(v["proj"]))
    return {p: min(vals) for p, vals in by.items() if vals}


def _status_seen(csv_players):
    """Did this paste actually carry DK's Status column?

    A filter that cannot see its input is not a filter, and it fails SILENTLY --
    every OUT and IR player just quietly becomes eligible. On a preseason slate
    that is close to guaranteed damage, because August rosters are full of them.
    So the answer travels to the UI rather than being assumed."""
    return any((c.get("status") or "").strip() for c in csv_players)


def showdown_pool(csv_players):
    """[{name, pos, team, salary, cpt_salary, proj}] -- one entry per PLAYER.

    The export lists everyone twice, so a naive parse doubles the pool and lets
    the same player be rostered as both his CPT and his FLEX self. Keyed by name
    with both salaries attached instead."""
    base, cpt_sal = {}, {}
    for c in csv_players:
        if not _playable(c):
            continue
        nm = c["name"]
        if (c.get("roster_pos") or "").upper() == "CPT":
            cpt_sal[nm] = int(c["salary"])
            base.setdefault(nm, c)
        else:
            base[nm] = c
    out = []
    for nm, c in base.items():
        pos = (c.get("pos") or "").upper().split("/")[0]
        pos = "RB" if pos == "FB" else pos
        if pos not in SHOWDOWN_POS:
            continue
        flex = int(c["salary"])
        out.append({"name": nm, "pos": pos, "team": (c.get("team") or "").strip(),
                    "salary": flex,
                    # If only the CPT row survived, its salary IS the flex one
                    # scaled up; and if only FLEX rows exist, DK's captain price
                    # is 1.5x by rule.
                    "cpt_salary": cpt_sal.get(nm, int(round(flex * CPT_MULT))),
                    "proj": float(c.get("proj") or 0.0)})
    return out


def _sd_fill(cands, budget, objective, rng, cpt_team, greedy=False):
    """Five FLEX under `budget`, spanning both teams with the captain."""
    picked, sal, teams = [], 0, {cpt_team}
    for slot in range(5):
        pool = [p for p in cands if p["_free"] and sal + p["salary"] <= budget]
        # Last slot and still one-sided -> it has to come from the other team.
        if slot == 4 and len(teams) < SHOWDOWN_MIN_TEAMS:
            other = [p for p in pool if p["team"] and p["team"] not in teams]
            if not other:
                return None
            pool = other
        if not pool:
            return None
        if greedy:
            pick = max(pool, key=lambda p: _value(p, objective))
        else:
            top = sorted(pool, key=lambda p: -_value(p, objective))[:16]
            w = [max(0.1, _value(x, objective)) ** 2 for x in top]
            pick = rng.choices(top, weights=w)[0]
        pick["_free"] = False
        picked.append(pick)
        sal += pick["salary"]
        if pick["team"]:
            teams.add(pick["team"])
    for p in picked:
        p["_free"] = True
    if len(teams) < SHOWDOWN_MIN_TEAMS:
        return None
    return picked, sal


def optimize_showdown(players, cap, objective, restarts=60, rng=None):
    """Best 1 CPT + 5 FLEX under the cap. Every player is tried as captain --
    the pool is one game, so that is only a few dozen -- and each captaincy gets
    one greedy fill plus a batch of randomized ones."""
    rng = rng or random
    if len(players) < len(SHOWDOWN_ROSTER):
        return None
    for p in players:
        p["_free"] = True
    best = None
    for cap_p in players:
        budget = cap - cap_p["cpt_salary"]
        if budget < 0:
            continue
        cap_p["_free"] = False
        others = [p for p in players if p is not cap_p]
        cap_val = CPT_MULT * _value(cap_p, objective)
        for i in range(restarts + 1):
            r = _sd_fill(others, budget, objective, rng, cap_p["team"], greedy=(i == 0))
            if not r:
                continue
            picked, sal = r
            score = cap_val + sum(_value(p, objective) for p in picked)
            if best is None or score > best[0]:
                best = (score, cap_p, list(picked), sal + cap_p["cpt_salary"])
        cap_p["_free"] = True
    if not best:
        return None
    _score, cap_p, picked, sal = best
    return cap_p, picked, sal


def _by_pos(players):
    by = {pos: [] for pos in set(ROSTER)}
    for p in players:
        for slot in p["elig"]:
            if slot in by:
                by[slot].append(p)
    for pos in by:
        by[pos].sort(key=lambda p: -p["proj"])
    if any(len(by[pos]) < ROSTER.count(pos) for pos in set(ROSTER)):
        return None
    return by


def _build_one(by_pos, cap, objective, stack_team=None, stack_min=0, rng=random):
    """One greedy-randomized valid lineup. With a stack, WR/TE/FLEX slots seed from
    the QB's team so the QB and his pass-catchers boom together."""
    used, lineup, sal, stacked = set(), [], 0, 0
    for si in sorted(range(len(ROSTER)), key=lambda i: len(by_pos[ROSTER[i]])):
        pos = ROSTER[si]
        pool = [p for p in by_pos[pos] if p["name"] not in used and sal + p["salary"] <= cap]
        if not pool:
            return None
        cand = pool
        # A real NFL stack is QB + a pass-catcher (WR/TE) from his team -- that's
        # where the sim's correlation lives (a QB's day lifts his receivers).
        if stack_team and pos in ("WR", "TE", "FLEX") and stacked < stack_min:
            team_pool = [p for p in pool if p.get("team") == stack_team and p["pos"] in ("WR", "TE")]
            if team_pool:
                cand = team_pool
        top = cand[:14]
        weights = [max(0.1, _value(x, objective)) ** 2 for x in top]
        pick = rng.choices(top, weights=weights)[0]
        if stack_team and pick.get("team") == stack_team and pick["pos"] in ("WR", "TE"):
            stacked += 1
        used.add(pick["name"]); lineup.append(pick); sal += pick["salary"]
    if len(lineup) != len(ROSTER) or sal > cap or (stack_team and stacked < stack_min):
        return None
    return lineup, sal


def optimize(players, cap, objective, stack_min=0, restarts=8000):
    by_pos = _by_pos(players)
    if by_pos is None:
        return None
    teams = sorted({p.get("team") for p in players if p.get("pos") == "QB" and p.get("team")})
    best = None
    for _ in range(restarts):
        st = random.choice(teams) if (stack_min and teams) else None
        r = _build_one(by_pos, cap, objective, stack_team=st, stack_min=stack_min, rng=random)
        if not r:
            continue
        lineup, sal = r
        score = sum(_value(p, objective) for p in lineup)
        if best is None or score > best[0]:
            best = (score, lineup, sal)
    return best[1] if best else None


# ---- ownership model --------------------------------------------------------
def _set_ownership(players, n_slots=None):
    """Rough field ownership from projection value (proj per $1k). Chalk = high
    value; used for the leverage objective and the contest field weighting.

    `n_slots` is the roster size the ownership has to add up across -- 9 in
    classic, 6 in showdown. Leaving it at 9 for a showdown slate would inflate
    every number by half."""
    n_slots = len(ROSTER) if n_slots is None else n_slots
    for p in players:
        v = p["proj"] / max(1.0, p["salary"] / 1000.0)
        p["_vw"] = max(0.01, v) ** 3.2
    tot = sum(p["_vw"] for p in players) or 1.0
    # Ownership across all players sums to ~n_slots x 100%; chalk (high value)
    # gets the bulk. Capped so no single player looks impossibly owned.
    for p in players:
        p["own"] = round(min(45.0, 100.0 * n_slots * p["_vw"] / tot), 1)


# ---- contest sim (win% / cash% / ROI, any field size) ----------------------
def _field_lineup(by_pos, cap, own_w, rng, tries=8):
    for _ in range(tries):
        used, names, sal, ok = set(), [], 0, True
        for pos in sorted(ROSTER, key=lambda p: len(by_pos[p])):
            pool = [p for p in by_pos[pos] if p["name"] not in used and sal + p["salary"] <= cap]
            if not pool:
                ok = False
                break
            w = [own_w.get(p["name"], 1.0) for p in pool]
            pick = rng.choices(pool, weights=w)[0]
            used.add(pick["name"]); names.append(pick["name"]); sal += pick["salary"]
        if ok and len(names) == len(ROSTER):
            return names
    return None


def contest_sim(your_lineup, players, contest="gpp", entry_fee=1.0, contest_size=None,
                prize_pool=None, first_prize=None, sample_size=500, n_iter=400):
    by_pos = _by_pos(players)
    arr = {p["name"]: p.get("arr") for p in players if p.get("arr")}
    if by_pos is None or not arr:
        return None
    L = min(len(a) for a in arr.values())
    own_w = {p["name"]: max(0.1, p.get("own", 8.0)) for p in players}
    proj_of = {p["name"]: p.get("proj", 0.0) for p in players}
    rng = random.Random(12345)
    ref = sum(proj_of.get(p["name"], 0.0) for p in your_lineup)
    floor_q = 0.84 * ref
    field, attempts = [], 0
    while len(field) < sample_size and attempts < sample_size * 40:
        attempts += 1
        fl = _field_lineup(by_pos, CAP, own_w, rng)
        if fl and sum(proj_of.get(nm, 0.0) for nm in fl) >= floor_q:
            field.append(fl)
    if len(field) < 30:
        return None

    C = max(2, int(contest_size or (len(field) + 1)))
    pool = float(prize_pool) if prize_pool else entry_fee * C * 0.85
    if contest == "double_up":
        places = max(1, int(round(0.45 * C)))
        each = pool / places
        payout = lambda r: each if r <= places else 0.0
        first = each
    else:
        first = float(first_prize) if first_prize else 0.20 * pool
        payout, places = _gpp_curve(C, pool, first, entry_fee)
    grid = _rank_grid(places)

    import statistics
    def score(names, it):
        return sum((arr.get(nm) or [0])[it % L] for nm in names)

    ynames = [p["name"] for p in your_lineup]
    win = cash = ret = top1 = 0.0
    top1_line = max(1, int(0.01 * C))
    for it in range(n_iter):
        fs = [score(f, it) for f in field]
        mu = statistics.fmean(fs); sd = statistics.pstdev(fs) or 1.0
        ys = score(ynames, it)
        q = max(1e-12, min(1.0, 1.0 - _ncdf((ys - mu) / sd)))
        winp = math.exp((C - 1) * math.log(1.0 - q)) if q < 1.0 else 0.0
        win += winp
        mr = 1.0 + (C - 1) * q
        sr = math.sqrt(max(1e-9, (C - 1) * q * (1.0 - q)))
        cash += _ncdf((places - mr) / sr)
        top1 += _ncdf((top1_line - mr) / sr)
        if contest == "double_up":
            ret += each * _ncdf((places - mr) / sr)
        else:
            ev = first * winp
            for r, wd in grid:
                ev += payout(r) * _npdf((r - mr) / sr) / sr * wd
            ret += ev
    ret /= n_iter
    return {"win_pct": round(100 * win / n_iter, 4), "cash_pct": round(100 * cash / n_iter, 1),
            "top1_pct": round(100 * top1 / n_iter, 2), "roi_pct": round(100 * (ret - entry_fee) / entry_fee, 1),
            "avg_return": round(ret, 2), "sample_size": len(field), "entries": C,
            "contest": contest, "entry_fee": entry_fee, "prize_pool": round(pool),
            "first_prize": round(first), "places_paid": places}


# ---- public build -----------------------------------------------------------
def _pct(a, q):
    s = sorted(a)
    return s[min(len(s) - 1, int(q * len(s)))]


def _special_arr(pos, preseason, n=1500):
    """Measured exhibition scoring for a kicker or a defense, or None.

    These two are the only units that play the whole game, and in preseason they
    are the highest-scoring positions on the board -- but neither was modelled
    for August. Kickers are not in the game sim at all, so they fell through to
    DK's AvgPointsPerGame, which is a REGULAR-SEASON average, plus an invented
    Gaussian. And dst_projections asks Sleeper for season_type=regular
    unconditionally, so a preseason DST carried a regular-season projection.
    Both now come from nfl_preseason's measured decile ladder."""
    if not preseason or (pos or "").upper() not in ("K", "DST"):
        return None
    try:
        import nfl_preseason
    except Exception:
        return None
    return nfl_preseason.special_samples(pos.upper(), n, random)


def _sd_field_lineup(players, cap, own_w, rng, tries=10):
    """One plausible opponent showdown roster: a captain drawn by ownership, then
    five FLEX the same way."""
    for _ in range(tries):
        cap_p = rng.choices(players, weights=[own_w.get(p["name"], 1.0) for p in players])[0]
        budget = cap - cap_p["cpt_salary"]
        if budget < 0:
            continue
        picked, sal, teams, used = [], 0, {cap_p["team"]}, {cap_p["name"]}
        for slot in range(5):
            pool = [p for p in players
                    if p["name"] not in used and sal + p["salary"] <= budget]
            if slot == 4 and len(teams) < SHOWDOWN_MIN_TEAMS:
                pool = [p for p in pool if p["team"] and p["team"] not in teams]
            if not pool:
                break
            pick = rng.choices(pool, weights=[own_w.get(p["name"], 1.0) for p in pool])[0]
            used.add(pick["name"]); picked.append(pick); sal += pick["salary"]
            if pick["team"]:
                teams.add(pick["team"])
        if len(picked) == 5 and len(teams) >= SHOWDOWN_MIN_TEAMS:
            return [("CPT", cap_p["name"])] + [("FLEX", p["name"]) for p in picked]
    return None


def _showdown_contest_sim(your, players, contest, entry_fee, contest_size,
                          prize_pool, first_prize, sample_size=400, n_iter=300):
    """Same maths as the classic contest sim, over showdown rosters. The captain's
    1.5x has to be applied per SLOT, not per player -- the same name scores
    differently depending on where the field put him."""
    import statistics
    arr = {p["name"]: p.get("arr") for p in players if p.get("arr")}
    if not arr:
        return None
    L = min(len(a) for a in arr.values())
    own_w = {p["name"]: max(0.1, p.get("own", 8.0)) for p in players}
    proj_of = {p["name"]: p.get("proj", 0.0) for p in players}
    rng = random.Random(12345)

    def sc(slots, it):
        return sum((CPT_MULT if s == "CPT" else 1.0) * (arr.get(nm) or [0])[it % L]
                   for s, nm in slots)

    ref = sum((CPT_MULT if s == "CPT" else 1.0) * proj_of.get(nm, 0.0) for s, nm in your)
    floor_q = 0.84 * ref
    field, attempts = [], 0
    while len(field) < sample_size and attempts < sample_size * 40:
        attempts += 1
        fl = _sd_field_lineup(players, CAP, own_w, rng)
        if fl and sum((CPT_MULT if s == "CPT" else 1.0) * proj_of.get(nm, 0.0)
                      for s, nm in fl) >= floor_q:
            field.append(fl)
    if len(field) < 30:
        return None
    C = max(2, int(contest_size or (len(field) + 1)))
    pool = float(prize_pool) if prize_pool else entry_fee * C * 0.85
    if contest == "double_up":
        places = max(1, int(round(0.45 * C)))
        each = pool / places
        payout = lambda r: each if r <= places else 0.0
        first = each
    else:
        first = float(first_prize) if first_prize else 0.20 * pool
        payout, places = _gpp_curve(C, pool, first, entry_fee)
    grid = _rank_grid(places)
    win = cash = ret = top1 = 0.0
    top1_line = max(1, int(0.01 * C))
    for it in range(n_iter):
        fs = [sc(f, it) for f in field]
        mu = statistics.fmean(fs); sd = statistics.pstdev(fs) or 1.0
        ys = sc(your, it)
        q = max(1e-12, min(1.0, 1.0 - _ncdf((ys - mu) / sd)))
        winp = math.exp((C - 1) * math.log(1.0 - q)) if q < 1.0 else 0.0
        win += winp
        mr = 1.0 + (C - 1) * q
        sr = math.sqrt(max(1e-9, (C - 1) * q * (1.0 - q)))
        cash += _ncdf((places - mr) / sr)
        top1 += _ncdf((top1_line - mr) / sr)
        if contest == "double_up":
            ret += each * _ncdf((places - mr) / sr)
        else:
            ev = first * winp
            for r, wd in grid:
                ev += payout(r) * _npdf((r - mr) / sr) / sr * wd
            ret += ev
    ret /= n_iter
    return {"win_pct": round(100 * win / n_iter, 4), "cash_pct": round(100 * cash / n_iter, 1),
            "top1_pct": round(100 * top1 / n_iter, 2),
            "roi_pct": round(100 * (ret - entry_fee) / entry_fee, 1),
            "avg_return": round(ret, 2), "sample_size": len(field), "entries": C,
            "contest": contest, "entry_fee": entry_fee, "prize_pool": round(pool),
            "first_prize": round(first), "places_paid": places}


def _build_showdown(csv_players, week, objective, contest, contest_size,
                    entry_fee, prize_pool, first_prize, preseason):
    ents = showdown_pool(csv_players)
    if len(ents) < len(SHOWDOWN_ROSTER):
        return {"error": f"showdown needs {len(SHOWDOWN_ROSTER)} available players "
                         f"(got {len(ents)} after dropping OUT/IR)"}
    pool = nfl_dfs_sim.player_pool(week, preseason=preseason) or {}
    _nidx, _norm = _norm_index(pool)
    _deep = _deep_fallback(pool, preseason)
    unmatched = []
    for e in ents:
        sp = _special_arr(e["pos"], preseason)
        sim = _pool_match(pool, e["name"], e["pos"], e.get("team"), _nidx, _norm)
        if sp:                                  # kicker / defense in August
            e["arr"] = sp
            e["proj"] = sum(sp) / len(sp)
            e["ceiling"], e["floor"] = round(_pct(sp, 0.9), 1), round(_pct(sp, 0.1), 1)
        elif sim and sim.get("arr"):
            e["proj"], e["ceiling"], e["floor"], e["arr"] = (
                sim["proj"], sim["ceiling"], sim["floor"], sim["arr"])
        else:
            pr = _deep.get(e["pos"], e["proj"])
            e["proj"] = pr
            e["arr"] = [max(0.0, random.gauss(pr, pr * 0.5 + 2)) for _ in range(1500)]
            e["ceiling"], e["floor"] = round(_pct(e["arr"], 0.9), 1), round(_pct(e["arr"], 0.1), 1)
            unmatched.append(e["name"])
        e["proj"] = round(e["proj"], 1)
    _set_ownership(ents, n_slots=len(SHOWDOWN_ROSTER))
    got = optimize_showdown(ents, CAP, objective)
    if not got:
        return {"error": "no valid showdown lineup under the cap"}
    cap_p, picked, sal = got
    slots = [("CPT", cap_p["name"])] + [("FLEX", p["name"]) for p in picked]
    lineup = [cap_p] + picked
    L = min(len(p["arr"]) for p in lineup)
    totals = [CPT_MULT * cap_p["arr"][i] + sum(p["arr"][i] for p in picked)
              for i in range(L)]
    csim = None
    if contest:
        try:
            csim = _showdown_contest_sim(slots, ents, contest, entry_fee, contest_size,
                                         prize_pool, first_prize)
        except Exception:
            csim = None
    rows = []
    for slot, p in [("CPT", cap_p)] + [("FLEX", q) for q in picked]:
        mult = CPT_MULT if slot == "CPT" else 1.0
        rows.append({"slot": slot, "name": p["name"], "pos": p["pos"], "team": p["team"],
                     "salary": p["cpt_salary"] if slot == "CPT" else p["salary"],
                     "proj": round(mult * p["proj"], 1),
                     "ceiling": round(mult * p["ceiling"], 1),
                     "floor": round(mult * p["floor"], 1), "own": p.get("own")})
    teams = sorted({p["team"] for p in lineup if p["team"]})
    # Flat pricing changes what the optimizer is solving. With one salary for the
    # whole pool the knapsack dissolves: there is no value play, no salary saver,
    # nothing to punt, and points-per-dollar is a constant. Every lineup that
    # fills six slots costs the same, so the only questions left are who plays
    # and who else will roster them. Worth saying out loud rather than leaving
    # the reader to notice the salary column never changes.
    _sals = {e["salary"] for e in ents}
    flat = len(_sals) == 1
    _sk = _status_seen(csv_players)
    return {"mode": "showdown", "roster": SHOWDOWN_ROSTER,
            "status_known": _sk,
            "status_warning": None if _sk else
            ("DK's Status column isn't in this paste, so OUT and IR players "
             "could NOT be excluded — check every name is active before you "
             "enter. Copy the whole CSV including its header row."),
            "flat_priced": flat,
            "flat_note": ("Every player on this slate is priced the same "
                          f"(${sorted(_sals)[0]:,} FLEX). Points per dollar is a "
                          "constant, so the cap can't distinguish lineups — the "
                          "whole decision is playing time and ownership.")
            if flat else None,
            "week": week, "objective": objective, "stack": False,
            "salary": sal, "cap": CAP, "salary_left": CAP - sal,
            "proj": round(sum(r["proj"] for r in rows), 1),
            "floor": round(_pct(totals, 0.10), 1),
            "median": round(_pct(totals, 0.50), 1),
            "ceiling": round(_pct(totals, 0.90), 1),
            "max": round(max(totals), 1),
            "lineup": rows, "contest_sim": csim,
            "unmatched": unmatched[:20], "n_pool": len(ents),
            "teams": teams,
            "note": "Showdown Captain Mode: 1 CPT at 1.5x points and 1.5x salary, "
                    "plus 5 FLEX from any position, spanning both teams. Players DK "
                    "lists OUT or IR are excluded."}


def build(csv_text, week=1, objective="projection", stack=True, contest=None,
          contest_size=None, entry_fee=1.0, prize_pool=None, first_prize=None,
          preseason=False, mode="auto"):
    csv_players = simulate.parse_dk_csv(csv_text)
    if not csv_players:
        return {"error": "couldn't read any players out of that CSV"}
    detected = detect_mode(csv_players)
    use = detected if mode in (None, "", "auto") else mode
    if use == "showdown":
        return _build_showdown(csv_players, week, objective, contest, contest_size,
                               entry_fee, prize_pool, first_prize, preseason)
    if len(csv_players) < len(ROSTER):
        return {"error": f"need at least {len(ROSTER)} players in the CSV (got {len(csv_players)})"}
    csv_players = [c for c in csv_players if _playable(c)]
    pool = nfl_dfs_sim.player_pool(week, preseason=preseason)
    if not pool:
        return {"error": "projections not ready — the weekly sim is still building, retry shortly"}

    _deep = _deep_fallback(pool, preseason)
    players, unmatched = [], []
    for c in csv_players:
        nm = c["name"]
        pos = (c.get("pos") or "").upper().split("/")[0]
        elig = _elig(pos)
        if not elig:
            continue
        sim = _pool_match(pool, nm, pos, c.get("team"), _nidx, _norm)
        _sp = _special_arr(pos, preseason)
        if _sp:                                     # kicker / defense in August
            samp = _sp
            proj = sum(_sp) / len(_sp)
            ceiling, floor = round(_pct(_sp, 0.9), 1), round(_pct(_sp, 0.1), 1)
        elif sim and sim.get("arr"):
            proj, ceiling, floor, samp = sim["proj"], sim["ceiling"], sim["floor"], sim["arr"]
        else:                                       # in the CSV but not projected -> soft fallback
            proj = _deep.get(pos, c.get("proj") or 0.0)
            samp = [max(0.0, random.gauss(proj, proj * 0.5 + 2)) for _ in range(1500)]
            ceiling, floor = round(_pct(samp, 0.9), 1), round(_pct(samp, 0.1), 1)
            unmatched.append(nm)
        players.append({"name": nm, "pos": pos, "team": c.get("team"), "salary": int(c["salary"]),
                        "proj": round(proj, 1), "ceiling": ceiling, "floor": floor,
                        "elig": elig, "arr": samp})
    if _by_pos(players) is None:
        return {"error": "the CSV doesn't cover every roster slot (need QB/RB/WR/TE/DST)"}

    _set_ownership(players)
    stack_min = 1 if (stack and objective in ("ceiling", "leverage")) else 0
    lineup = optimize(players, CAP, objective, stack_min=stack_min)
    if not lineup:
        return {"error": "no valid lineup under the cap"}

    # lineup distribution from the correlated arrays
    L = min(len(p["arr"]) for p in lineup)
    totals = [sum(p["arr"][i] for p in lineup) for i in range(L)]
    csim = None
    if contest:
        try:
            csim = contest_sim(lineup, players, contest=contest, entry_fee=entry_fee,
                               contest_size=contest_size, prize_pool=prize_pool, first_prize=first_prize)
        except Exception:
            csim = None
    slot_of, order = [], {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "FLEX": 4, "DST": 5}
    # label FLEX: the extra RB/WR/TE beyond the required count
    need = {"RB": 2, "WR": 3, "TE": 1}
    seen = {"RB": 0, "WR": 0, "TE": 0}
    rows = []
    for p in sorted(lineup, key=lambda x: (order.get(x["pos"], 9), -x["proj"])):
        slot = p["pos"]
        if p["pos"] in need:
            seen[p["pos"]] += 1
            if seen[p["pos"]] > need[p["pos"]]:
                slot = "FLEX"
        rows.append({"slot": slot, "name": p["name"], "pos": p["pos"], "team": p["team"],
                     "salary": p["salary"], "proj": p["proj"], "ceiling": p["ceiling"],
                     "floor": p["floor"], "own": p.get("own")})
    _sk = _status_seen(csv_players)
    return {"mode": "classic", "roster": ROSTER,
            "status_known": _sk,
            "status_warning": None if _sk else
            ("DK's Status column isn't in this paste, so OUT and IR players "
             "could NOT be excluded — check every name is active before you "
             "enter. Copy the whole CSV including its header row."),
            "week": week, "objective": objective, "stack": bool(stack_min),
            "salary": sum(p["salary"] for p in lineup), "cap": CAP,
            "proj": round(sum(p["proj"] for p in lineup), 1),
            "floor": round(_pct(totals, 0.10), 1), "median": round(_pct(totals, 0.50), 1),
            "ceiling": round(_pct(totals, 0.90), 1), "max": round(max(totals), 1),
            "lineup": rows, "contest_sim": csim, "unmatched": unmatched[:20],
            "n_pool": len(players),
            "note": "Projections pinned to Sleeper; floor/ceiling + QB-WR correlation from the "
                    "game sim. Ownership is a model estimate of the field."}
