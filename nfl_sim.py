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


def _scaled(val, par, spread=0.28):
    """Map a value to 0-100 by its ratio to a par baseline (par -> 50)."""
    if not par:
        return 50.0
    return max(0.0, min(100.0, 50 + (val / par - 1) / spread * 50))


# Best-ball overall weighting: ceiling & starting power dominate (boom weeks win
# best ball), with depth, stars, and stacks as the next tier.
_CAT_W = {"ceiling": 0.22, "starting": 0.20, "depth": 0.15, "stars": 0.13,
          "stacks": 0.10, "balance": 0.08, "floor": 0.07, "upside": 0.05}
# Ideal roster construction for an 18-pick best-ball team.
_IDEAL = {"QB": (2, 3), "RB": (5, 7), "WR": (6, 9), "TE": (2, 3)}


def _positional_ranks(pool):
    pr = {}
    for pos in _FANTASY_POS:
        lst = sorted((p for p in pool if p.get("pos") == pos), key=lambda x: -(x.get("season") or 0))
        for i, p in enumerate(lst, 1):
            pr[(pos, _gkey(p.get("name")))] = i
    return pr


def grade_roster(roster, pool=None, use_llm=False):
    """Grade a best-ball roster across eight categories, with per-position
    explanations, stacks, a plain-English narrative (optionally LLM-written), and
    an overall letter + 0-100 score benchmarked against a field of chalk ADP teams."""
    roster = [p for p in roster if p and p.get("pos") in _FANTASY_POS]
    if len(roster) < 4:
        return {"error": "Add at least a few players to grade a team."}
    if pool is None:
        b = board()
        pool = (b or {}).get("pool") or []

    st_proj, proj = _optimal(roster, "fppg")
    _, ceil = _optimal(roster, "boom")
    _, floor = _optimal(roster, "floor")
    starters = st_proj
    start_ids = {id(p) for p in starters}

    # Par baselines from the chalk-ADP field.
    par = _par_teams(pool) if pool else []
    par_proj = (sum(_optimal(t, "fppg")[1] for t in par) / len(par)) if par else proj
    par_ceil = (sum(_optimal(t, "boom")[1] for t in par) / len(par)) if par else ceil
    par_floor = (sum(_optimal(t, "floor")[1] for t in par) / len(par)) if par else floor

    pool_rank = _positional_ranks(pool)
    counts = {pos: sum(1 for p in roster if p.get("pos") == pos) for pos in _FANTASY_POS}

    # ---- Per-position analysis + "why good / why bad" notes ----
    positions = {}
    _good = {"QB": 8, "RB": 18, "WR": 24, "TE": 8}
    _elite = {"QB": 4, "RB": 8, "WR": 10, "TE": 4}
    for pos in ("QB", "RB", "WR", "TE"):
        grp = sorted((p for p in roster if p.get("pos") == pos), key=lambda x: -(x.get("season") or 0))
        n = len(grp)
        starters_here = [p for p in grp if id(p) in start_ids]
        ranks = [pool_rank.get((pos, _gkey(p.get("name")))) for p in starters_here]
        ranks = [r for r in ranks if r]
        avg_rank = (sum(ranks) / len(ranks)) if ranks else None
        thin = n < _DEPTH_TARGET[pos]
        if avg_rank is None:
            pgrade, note = "F", f"No starting-caliber {pos} — you can't fill the slot."
        elif avg_rank <= _elite[pos]:
            pgrade = "A"
            note = f"Elite — {grp[0]['name']} anchors a top-tier {pos} room" + \
                   ("." if not thin else f", but only {n} deep (rest weeks are exposed).")
        elif avg_rank <= _good[pos]:
            pgrade = "B"
            note = f"Solid — {grp[0]['name']} leads a startable group" + \
                   (f", with good depth ({n})." if not thin else f", but thin at {n}.")
        elif avg_rank <= _good[pos] * 1.8:
            pgrade = "C"
            note = f"Middling — {grp[0]['name']} is a fine starter but there's little upside behind."
        else:
            pgrade = "D"
            note = f"Weak — your {pos} starters sit below the startable line; a tournament-killer."
        if thin and pgrade not in ("A", "B"):
            note += f" Only {n} rostered (want {_DEPTH_TARGET[pos]}+) — dead weeks loom on byes."
        positions[pos] = {"count": n, "grade": pgrade, "thin": thin,
                          "avg_starter_rank": round(avg_rank, 1) if avg_rank else None,
                          "best": grp[0]["name"] if grp else None, "note": note}

    # ---- Stacks (correlated ceiling) ----
    stacks = []
    qbs = [p for p in roster if p.get("pos") == "QB"]
    catchers = [p for p in roster if p.get("pos") in ("WR", "TE")]
    for qb in qbs:
        mates = [c["name"] for c in catchers if c.get("team") and c.get("team") == qb.get("team")]
        if mates:
            stacks.append({"qb": qb["name"], "team": qb.get("team"), "partners": mates})
    n_stack_partners = sum(len(s["partners"]) for s in stacks)

    # ---- Star anchors (top-of-draft, league-winning players) ----
    anchors = [p for p in roster if (p.get("adp") or 999) <= 24]
    studs = [p for p in roster if (p.get("adp") or 999) <= 60]

    # ---- Roster-construction archetype ----
    rb, wr = counts["RB"], counts["WR"]
    if rb >= 7 and wr <= 6:
        archetype = "RB-heavy"
    elif rb <= 4 and wr >= 8:
        archetype = "Zero-RB / WR-heavy"
    elif rb <= 5 and len(anchors) and anchors[0]["pos"] == "RB":
        archetype = "Hero-RB"
    else:
        archetype = "Balanced"

    # ---- Risk / upside (rookies + injury bounce-backs) ----
    rookies = [p for p in roster if p.get("rookie")]
    returnees = [p for p in roster if p.get("injury_return") or p.get("fa") or p.get("new_team")]
    n_dart = len(rookies) + len(returnees)

    # ---- Category scores (0-100) ----
    cats = {}
    cats["starting"] = _scaled(proj, par_proj)
    cats["ceiling"] = _scaled(ceil, par_ceil)
    cats["floor"] = _scaled(floor, par_floor)
    # depth: coverage vs target across positions
    cov = sum(min(1.0, counts[p] / _DEPTH_TARGET[p]) for p in _DEPTH_TARGET) / len(_DEPTH_TARGET)
    cats["depth"] = round(cov * 100, 1)
    # stacks: 0 partners -> 40, scaling up
    cats["stacks"] = min(100.0, 40 + 18 * n_stack_partners)
    # balance: penalize each position outside its ideal band
    bal = 100.0
    for pos, (lo, hi) in _IDEAL.items():
        c = counts[pos]
        if c < lo:
            bal -= 12 * (lo - c)
        elif c > hi:
            bal -= 6 * (c - hi)
    cats["balance"] = max(0.0, bal)
    # stars: anchors + studs
    cats["stars"] = min(100.0, 45 + 22 * len(anchors) + 6 * (len(studs) - len(anchors)))
    # upside: best around 3-4 darts; too few = low swing, too many = volatile
    cats["upside"] = max(20.0, 100 - 14 * abs(n_dart - 3.5))

    overall = sum(cats[k] * w for k, w in _CAT_W.items())
    score = round(max(0, min(100, overall)))

    cat_labels = {"starting": ("🎯", "Starting Power"), "ceiling": ("🚀", "Ceiling"),
                  "floor": ("🛡️", "Floor"), "depth": ("📚", "Depth"),
                  "stacks": ("🔗", "Stacks"), "balance": ("⚖️", "Construction"),
                  "stars": ("⭐", "Star Power"), "upside": ("🎲", "Upside")}
    cat_why = {
        "starting": f"Your best weekly lineup projects {proj:.0f} pts ({'above' if proj >= par_proj else 'below'} the ~{par_proj:.0f} field average).",
        "ceiling": f"Boom-week ceiling of {ceil:.0f} pts — {'spike weeks win best ball, and you have them' if ceil >= par_ceil else 'short on the smash weeks that win best ball'}.",
        "floor": f"Floor lineup of {floor:.0f} pts — {'stable enough to avoid dead weeks' if floor >= par_floor*0.97 else 'some weeks could crater'}.",
        "depth": f"{int(cov*100)}% of healthy depth — " + ("you can fill every lineup across byes." if cov >= 0.9 else "you'll have holes when starters rest or bust."),
        "stacks": (f"{len(stacks)} stack(s): " + "; ".join(f"{s['qb']}+{', '.join(s['partners'])}" for s in stacks)) if stacks else "No QB↔pass-catcher stacks — leaving correlated ceiling on the table.",
        "balance": f"{archetype} build ({counts['QB']}QB/{counts['RB']}RB/{counts['WR']}WR/{counts['TE']}TE).",
        "stars": (f"{len(anchors)} early-round anchor(s): " + ", ".join(a["name"] for a in anchors[:3])) if anchors else "No top-24 anchors — you lack a true league-winner.",
        "upside": f"{len(rookies)} rookies + {len(returnees)} bounce-back/new-role fliers — " + ("a healthy dose of dart throws." if 2 <= n_dart <= 5 else "very few swing picks." if n_dart < 2 else "high variance; make sure the base is stable."),
    }
    categories = [{"key": k, "emoji": cat_labels[k][0], "label": cat_labels[k][1],
                   "score": round(cats[k]), "grade": _letter(cats[k]), "why": cat_why[k]}
                  for k in ("starting", "ceiling", "depth", "stars", "stacks", "balance", "floor", "upside")]

    # ---- Strengths / weaknesses from the category + position reads ----
    strengths, weaknesses = [], []
    for c in sorted(categories, key=lambda c: -c["score"]):
        if c["score"] >= 70 and len(strengths) < 3:
            strengths.append(f"{c['emoji']} {c['label']}: {c['why']}")
    for c in sorted(categories, key=lambda c: c["score"]):
        if c["score"] <= 50 and len(weaknesses) < 3:
            weaknesses.append(f"{c['emoji']} {c['label']}: {c['why']}")
    for pos, v in positions.items():
        if v["thin"] and len(weaknesses) < 4:
            weaknesses.append(f"Thin at {pos} ({v['count']}) — {v['note']}")

    # ---- Narrative (templated; LLM optionally upgrades it) ----
    best_cat = max(categories, key=lambda c: c["score"])
    worst_cat = min(categories, key=lambda c: c["score"])
    anchor_txt = (", ".join(a["name"] for a in anchors[:2]) or starters[0]["name"]) if (anchors or starters) else "your core"
    narrative = (f"A {archetype.lower()} build anchored by {anchor_txt}. "
                 f"Its biggest edge is {best_cat['label'].lower()} ({best_cat['grade']}); "
                 f"the soft spot is {worst_cat['label'].lower()} ({worst_cat['grade']}). "
                 + ("With stacks and a high ceiling, it's built to spike. " if cats["ceiling"] >= 65 and stacks
                    else "Shore up the weak spots and it's a contender. "))

    out = {
        "grade": _letter(score), "score": score,
        "proj_week": round(proj, 1), "ceiling_week": round(ceil, 1), "floor_week": round(floor, 1),
        "n_players": len(roster), "counts": counts, "archetype": archetype,
        "categories": categories, "positions": positions, "stacks": stacks,
        "anchors": [a["name"] for a in anchors],
        "starters": [{"name": p["name"], "pos": p["pos"], "team": p.get("team"),
                      "fppg": p.get("fppg"), "boom": p.get("boom")} for p in starters],
        "strengths": strengths, "weaknesses": weaknesses,
        "vs_field": round((proj / par_proj - 1) * 100, 1) if par_proj else 0,
        "narrative": narrative,
    }
    if use_llm:
        llm = _llm_narrative(out)
        if llm:
            out["llm_narrative"] = llm
    return out


def _llm_narrative(g):
    """Optional: a sharp 2-3 sentence analyst take from a small LLM. Only fires when
    an ANTHROPIC_API_KEY is configured; otherwise the templated narrative stands."""
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import json
        import anthropic
        brief = {
            "grade": g["grade"], "score": g["score"], "archetype": g["archetype"],
            "anchors": g["anchors"], "proj_week": g["proj_week"], "ceiling_week": g["ceiling_week"],
            "counts": g["counts"], "stacks": [f"{s['qb']}+{','.join(s['partners'])}" for s in g["stacks"]],
            "categories": {c["label"]: c["grade"] for c in g["categories"]},
            "positions": {p: v["grade"] for p, v in g["positions"].items()},
        }
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-haiku-4-5", max_tokens=200,
            system=("You are a sharp, concise fantasy-football analyst grading a best-ball draft team. "
                    "In 2-3 punchy sentences, say why the team is good or bad and the single biggest "
                    "thing to note (a strength to lean on or a hole to worry about). Be specific, name "
                    "players, don't just restate numbers, no preamble."),
            messages=[{"role": "user", "content": json.dumps(brief)}])
        return "".join(b.text for b in msg.content if b.type == "text").strip() or None
    except Exception:
        return None


def grade_names(names, use_llm=False):
    """Grade a roster given player NAMES. Resolves each against the projection pool
    (duplicates across separately-graded teams are fine). Returns (grade, unmatched)."""
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
    g = grade_roster(roster, pool, use_llm=use_llm)
    g["unmatched"] = unmatched
    g["matched"] = [p["name"] for p in roster]
    return g, unmatched


def grade_multi(teams, use_llm=False):
    """Grade several teams and rank them. `teams` = [{label, names}]. Each team is
    resolved independently, so the SAME player can appear on multiple teams (9
    drafters, 9 separate rosters). Adds a leaderboard + per-category 'who's best'."""
    b = board()
    pool = (b or {}).get("pool") or []
    if not pool:
        return None
    idx = {_gkey(p.get("name")): p for p in pool}
    graded = []
    for i, t in enumerate(teams):
        names = t.get("names") or []
        roster = [idx[_gkey(nm)] for nm in names if _gkey(nm) in idx]
        unmatched = [nm for nm in names if nm.strip() and _gkey(nm) not in idx]
        g = grade_roster(roster, pool, use_llm=use_llm)
        g["label"] = t.get("label") or f"Team {i + 1}"
        g["unmatched"] = unmatched
        graded.append(g)
    # rank by score
    ranked = sorted([g for g in graded if not g.get("error")], key=lambda g: -g["score"])
    for r, g in enumerate(ranked, 1):
        g["rank"] = r
    # category leaders (who's best at each)
    leaders = {}
    if ranked:
        for c in ("starting", "ceiling", "depth", "stars", "stacks", "balance", "floor", "upside"):
            best = max(ranked, key=lambda g: next((x["score"] for x in g["categories"] if x["key"] == c), 0))
            leaders[c] = best["label"]
    return {"teams": ranked, "errors": [g for g in graded if g.get("error")],
            "category_leaders": leaders, "n": len(ranked)}


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
