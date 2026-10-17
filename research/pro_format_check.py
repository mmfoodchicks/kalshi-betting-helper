"""Replay real NBA and NHL seasons through pro_season and require reality.

For each finished season, every real regular-season result (ESPN's monthly
scoreboards through pro_season.espn_games -- the loader the season job
uses; NHL overtime and shootout from the period the game ended in) goes
into pro_season.Table, and the module's own standings, tiebreakers and
playoff format are checked against what actually happened:

  * the conference order (all 15 / 16 places) against ESPN's final
    standings (`playoffSeed`), place by place;
  * NHL: the eight first-round pairs against the clubs that actually met
    in round one (ESPN's postseason scoreboards);
  * NBA: the eight play-in clubs against the clubs that actually played
    the play-in, and the 7-8 / 9-10 pairs carried through the real play-in
    results to ESPN's final seeds (ESPN's `playoffSeed` is post-play-in).

A mismatch prints the conference, the two orders and the clubs' records,
so a disagreement is read, not counted. Divisions and conferences are
ESPN's for that season (Utah took Arizona's place in the Central in
2024-25). The play-by-play stores are not used: the NBA store is short a
few games a season and the NHL store spells five clubs the league's way.

Usage: python3 -m research.pro_format_check <nba|nhl> <cache dir> [--seasons 2023 2024 2025]
(`season` is the year the season STARTS.) Exits 1 when any check fails.
"""
import json
import os
import random
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import pro_season as PS  # noqa: E402


def _get(url, tries=4):
    import time
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 ** (i + 1))


def espn_alignment(league, season):
    """({abbr: conf}, {abbr: div}, {conf: [abbr by ESPN's final order]})
    for the season that ENDS in season + 1."""
    path = "basketball/nba" if league == "nba" else "hockey/nhl"
    d3 = _get(f"https://site.api.espn.com/apis/v2/sports/{path}/standings?season={season + 1}&level=3")
    conf, div = {}, {}
    for c in d3.get("children") or []:
        for dv in c.get("children") or []:
            for e in (dv.get("standings") or {}).get("entries") or []:
                ab = e["team"]["abbreviation"]
                conf[ab] = c["name"]
                div[ab] = dv["name"]
    d2 = _get(f"https://site.api.espn.com/apis/v2/sports/{path}/standings?season={season + 1}")
    order = {}
    for c in d2.get("children") or []:
        rows = []
        for e in (c.get("standings") or {}).get("entries") or []:
            st = {s["name"]: s.get("value") for s in e.get("stats") or []}
            rows.append((st.get("playoffSeed") or 99, e["team"]["abbreviation"]))
        order[c["name"]] = [ab for _s, ab in sorted(rows)]
    return conf, div, order


def _cached_get(cache):
    os.makedirs(cache, exist_ok=True)

    def get(url, timeout=40):
        f = os.path.join(cache, "".join(ch if ch.isalnum() else "_" for ch in url)[-150:] + ".json")
        if os.path.exists(f):
            return json.load(open(f))
        d = _get(url)
        json.dump(d, open(f, "w"))
        return d
    return get


def load_games(league, cache, season):
    """(regular-season games, postseason games) of the season starting in
    `season`, ESPN's abbreviations."""
    get = _cached_get(cache)
    reg = [g for g in PS.espn_games(league, season + 1, get=get) if g["final"]]
    post = []
    for m in (4, 5, 6):
        d = get(f"https://site.api.espn.com/apis/site/v2/sports/{PS._PATH[league]}/scoreboard"
                f"?dates={season + 1}{m:02d}&limit=1000")
        for e in d.get("events") or []:
            stype = (e.get("season") or {}).get("type")
            if stype not in (3, 5):
                continue
            c = e["competitions"][0]
            side = {x["homeAway"]: x for x in c["competitors"]}
            post.append({"id": e["id"], "date": e["date"], "stype": stype,
                         "home": side["home"]["team"]["abbreviation"],
                         "away": side["away"]["team"]["abbreviation"],
                         "hs": int(side["home"].get("score") or 0), "as": int(side["away"].get("score") or 0)})
    post.sort(key=lambda g: (g["date"], g["id"]))
    return reg, post


def check(league, cache, season, log=print):
    conf, div, espn_order = espn_alignment(league, season)
    reg, post = load_games(league, cache, season)
    T = PS.Table(league, conf, div)
    unknown = set()
    for g in reg:
        if g["home"] not in conf or g["away"] not in conf:
            unknown.update(x for x in (g["home"], g["away"]) if x not in conf)
            continue
        T.add(g["home"], g["away"], g["hs"], g["as"], g["kind"])
    rng = random.Random(1)
    fails = []
    gp = sorted(set(T.gp(t) for t in conf))
    if unknown:     # the All-Star games' sides; anything else is a real club missing
        log(f"    (left out, not in the alignment: {sorted(unknown)})")
    if gp != [82]:
        fails.append(f"games played per club {gp} (expected 82 each)")
    if league == "nhl":
        field = PS.nhl_field(T, rng)
        for c, f in field.items():
            if f["order"] != espn_order.get(c):
                fails.append(_diff(T, c, f["order"], espn_order.get(c)))
        firsts = _first_round_pairs([g for g in post if g["stype"] == 3])
        mine = set()
        for c, f in field.items():
            for side in f["sides"]:
                for a, b in side["pairs"]:
                    mine.add(frozenset((a, b)))
        if firsts and mine != firsts:
            fails.append(f"first round: model {sorted(map(sorted, mine - firsts))} vs real {sorted(map(sorted, firsts - mine))}")
        summary = {c: {"div": f["div"], "wc": f["wc"]} for c, f in field.items()}
    else:
        field = PS.nba_field(T, rng)
        # ESPN's final `playoffSeed` is the seed AFTER the play-in (the 7-8
        # game's winner is 7, the last game's winner 8, the two clubs knocked
        # out 9 and 10 in their regular-season order -- 2024-25 West: SAC
        # stayed 9th though DAL went out a round later), so the regular-season
        # order is carried through the real play-in results before it is
        # compared
        playin = [g for g in post if g["stype"] == 5]
        won = {}
        for g in playin:
            won[frozenset((g["home"], g["away"]))] = g["home"] if g["hs"] > g["as"] else g["away"]
        for c, f in field.items():
            o = f["order"]
            s7, s8, s9, s10 = o[6:10]
            seeds = list(o)
            w78 = won.get(frozenset((s7, s8)))
            w910 = won.get(frozenset((s9, s10)))
            if w78 and w910:
                l78 = s8 if w78 == s7 else s7
                l910 = s10 if w910 == s9 else s9
                w8 = won.get(frozenset((l78, w910)))
                if w8:
                    out_ = sorted((l78 if w8 == w910 else w910, l910), key=o.index)
                    seeds[6:10] = [w78, w8] + out_
                else:
                    fails.append(f"{c}: no real game between the 7-8 loser {l78} and the 9-10 winner {w910}")
            else:
                fails.append(f"{c}: the model's 7-8 ({s7}, {s8}) or 9-10 ({s9}, {s10}) pairs never played")
            if seeds != espn_order.get(c):
                fails.append(_diff(T, c, seeds, espn_order.get(c)))
        real_playin = set()
        for g in playin:
            real_playin.update((g["home"], g["away"]))
        mine_playin = set(t for f in field.values() for t in f["order"][6:10])
        if real_playin and mine_playin != real_playin:
            fails.append(f"play-in: model {sorted(mine_playin - real_playin)} vs real {sorted(real_playin - mine_playin)}")
        summary = {c: {"top6": f["order"][:6], "playin": f["order"][6:10]} for c, f in field.items()}
    # the real ties the season handed the tiebreakers (equal points / record
    # inside a conference): how much of the rulebook this season tested
    ties = []
    for c in sorted(set(conf.values())):
        members = [t for t in conf if conf[t] == c]
        by = {}
        for t in members:
            by.setdefault(T.pts[t] if league == "nhl" else T.w[t], []).append(t)
        ties += [sorted(v) for v in by.values() if len(v) > 1]
    log(f"    ties broken: {len(ties)} groups {ties}")
    log(f"[format-{league}] {season}-{(season + 1) % 100:02d}: {len(reg)} regular-season games, "
        f"{'OK' if not fails else f'{len(fails)} MISMATCH(ES)'}")
    for f in fails:
        log("    " + f)
    return {"season": season, "games": len(reg), "ok": not fails, "fails": fails, "summary": summary,
            "ties": ties}


def _first_round_pairs(post):
    """The pairs that met in each club's first playoff game."""
    seen, pairs = set(), set()
    for g in post:
        h, a = g["home"], g["away"]
        if h in seen or a in seen:
            continue
        seen.update((h, a))
        pairs.add(frozenset((h, a)))
    return pairs


def _diff(T, c, mine, real):
    rows = []
    for i in range(max(len(mine), len(real or []))):
        m = mine[i] if i < len(mine) else "-"
        r = (real or [])[i] if i < len(real or []) else "-"
        if m != r:
            rec = lambda t: (f"{T.w.get(t)}-{T.l.get(t)}" + (f"-{T.otl[t]} {T.pts[t]}p RW{T.rw[t]} ROW{T.row[t]}"
                                                            if T.league == "nhl" else "")) if t in T.w else "?"
            rows.append(f"#{i + 1} model {m} ({rec(m)}) / real {r} ({rec(r)})")
    return f"{c}: " + "; ".join(rows)


def replay_fixture(d, rng=None):
    """The offline check on a committed fixture: [] when pro_season, fed the
    season's real results, gives ESPN's final conference order place by
    place (NBA: through the real play-in) and, for the NHL, the real
    first-round pairs; else the mismatches. The guard suite's replay."""
    league = d["league"]
    T = PS.Table(league, d["conf"], d["div"])
    for h, a, hs, as_, kind in d["games"]:
        T.add(h, a, hs, as_, kind)
    rng = rng or random.Random(1)
    fails = []
    if league == "nhl":
        field = PS.nhl_field(T, rng)
        for c, f in field.items():
            if f["order"] != d["espn_order"].get(c):
                fails.append(_diff(T, c, f["order"], d["espn_order"].get(c)))
        firsts = _first_round_pairs([{"home": h, "away": a} for st, h, a, _hs, _as in d["post"] if st == 3])
        mine = set(frozenset(p) for f in field.values() for side in f["sides"] for p in side["pairs"])
        if mine != firsts:
            fails.append("first round differs")
        return fails, T, field
    field = PS.nba_field(T, rng)
    won = {frozenset((h, a)): (h if hs > as_ else a) for st, h, a, hs, as_ in d["post"] if st == 5}
    for c, f in field.items():
        o = f["order"]
        s7, s8, s9, s10 = o[6:10]
        w78, w910 = won.get(frozenset((s7, s8))), won.get(frozenset((s9, s10)))
        if not (w78 and w910):
            fails.append(f"{c}: play-in pairs never played")
            continue
        l78 = s8 if w78 == s7 else s7
        l910 = s10 if w910 == s9 else s9
        w8 = won.get(frozenset((l78, w910)))
        seeds = list(o)
        seeds[6:10] = [w78, w8] + sorted((l78 if w8 == w910 else w910, l910), key=o.index)
        if seeds != d["espn_order"].get(c):
            fails.append(_diff(T, c, seeds, d["espn_order"].get(c)))
    return fails, T, field


def fixture(league, cache, season):
    """The compact replay the guard suite reads offline: the alignment,
    every regular-season result, ESPN's final order, the real play-in /
    first-round games. research/data/fixtures/pro_season_<league>_<season>.json"""
    conf, div, espn_order = espn_alignment(league, season)
    reg, post = load_games(league, cache, season)
    d = {"league": league, "season": season, "conf": conf, "div": div, "espn_order": espn_order,
         "games": [[g["home"], g["away"], g["hs"], g["as"], g["kind"]] for g in reg
                   if g["home"] in conf and g["away"] in conf],
         "post": [[g["stype"], g["home"], g["away"], g["hs"], g["as"]] for g in post
                  if g["stype"] == 5 or (league == "nhl" and g["stype"] == 3)]}
    out = os.path.join(ROOT, "research", "data", "fixtures", f"pro_season_{league}_{season}.json")
    with open(out, "w") as f:
        json.dump(d, f, separators=(",", ":"))
    return out


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("league", choices=("nba", "nhl"))
    ap.add_argument("cache")
    ap.add_argument("--seasons", type=int, nargs="*", default=[2023, 2024, 2025])
    ap.add_argument("--fixture", type=int, default=None, help="write the guard fixture for this season")
    a = ap.parse_args(argv)
    if a.fixture:
        print(fixture(a.league, a.cache, a.fixture))
        return
    res = [check(a.league, a.cache, s) for s in a.seasons]
    out = os.path.join(ROOT, "research", "data", f"pro_format_check_{a.league}.json")
    json.dump(res, open(out, "w"), indent=1, default=list)
    sys.exit(0 if all(r["ok"] for r in res) else 1)


if __name__ == "__main__":
    main(sys.argv[1:])
