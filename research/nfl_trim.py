"""The drive sim's trims, refit on the season being played (2026-10-19).

nfl_game_sim's level trims (_TD_CAL, _FG_CAL, the points level they land on)
and its per-stat player pins (_PROJ_BIAS) were fitted on 2025, the season
Sleeper's projections ran HOT: touchdowns 0.94-0.95x, points 0.969x, passing
0.975x, receiving yards 0.931x. Applied to 2026 they assume the same optimism,
and 2026's Sleeper is not optimistic. Read on 2026-10-19, before this was
written:
  * results, 2026 weeks 1-6 (92 games, the drive sim replayed on Sleeper's
    week projections): real totals 46.24, Sleeper's projected 45.80
    (+0.44 +/- 1.42), the sim's 44.02 (+2.21 +/- 1.39 low);
  * Kalshi, the week-7 slate: the sim's median total under Kalshi's
    de-vigged median on every game where both cross 50% (8 of 8, mean
    -2.72 points), and its P(over) under on 12 of 14 games;
  * Sleeper's 2026 table (weeks 1-6, the definitions below, which reproduce
    2025's shipped table: passing 0.974 on the same 543 QB-games, receiving
    yards 0.928, receptions 0.975): touchdowns 1.048, field goals 1.029,
    passing 1.011, rushing 0.918, receiving yards 0.977, receptions 1.020.

THE REFIT, decided before it was run:
  - targets: touchdowns realized at the 2026 TD ratio x the projection; then
    points at the 2026 actual/projected points ratio x nfl_data's points
    (offence + the DST sliver) -- the two-step fit the 2025 trims used, on
    the week-7 slate's profiles at 2,000 games a game, common random numbers;
  - _PROJ_BIAS: the 2026 per-stat ratios as measured.
  THE RULE: the 2026 trims ship when, on week 7's games whose Kalshi total
  ladder and sim both cross 50%, the refit sim's mean signed gap (sim median
  minus Kalshi's) lies within +/-1.5 points AND is smaller in magnitude than
  the shipped trims' gap on the same games. Kalshi's numbers are not held
  out (they were read to find this), but the fit never reads them: they are
  the independent check. Otherwise nothing changes and the report says why.

Definitions. Team-level: every projected QB/RB/WR/TE/K who played (a stat row
with attempts, targets or kicks), summed per team-game, Sleeper's projections
against Sleeper's box; points are ESPN's finals against nfl_data's projected
team points. Player-level: projected passing >= 150 yards, rushing >= 25,
receiving >= 25, receptions >= 2.5 (the sim's own prop floors), played only;
ratio = sum actual / sum projected.

python3 -m research.nfl_trim [season] [last week played] [fit week]
"""
import json
import math
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "research", "data", "nfl_trim_2026.json")
SB = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
PJ = "https://api.sleeper.com/projections/nfl/{s}/{w}?season_type=regular&{q}"
ST = "https://api.sleeper.com/stats/nfl/{s}/{w}?season_type=regular&{q}"
POS = "&".join(f"position[]={p}" for p in ("QB", "RB", "WR", "TE", "K"))
FLOORS = (("pass_yd", 150.0), ("rush_yd", 25.0), ("rec_yd", 25.0), ("rec", 2.5))
FIT_N, SEED, SHIP_GAP = 2000, 11, 1.5


def table(season, weeks, log=print):
    """Sleeper's projections against its box, the definitions above."""
    import racing

    def get(u):
        return {r.get("player_id"): r for r in (racing._get_json(u, timeout=30) or [])}
    pl = {k: [0.0, 0.0, 0] for k, _f in FLOORS}
    team = {}
    for w in weeks:
        pj, st = get(PJ.format(s=season, w=w, q=POS)), get(ST.format(s=season, w=w, q=POS))
        for pid, r in pj.items():
            a = st.get(pid) or {}
            pp, ss = r.get("stats") or {}, a.get("stats") or {}
            tm = r.get("team") or a.get("team")
            if not tm or not ss or not any(ss.get(k) for k in ("pass_att", "rush_att", "rec_tgt", "fga", "xpa")):
                continue
            t = team.setdefault((w, tm), {"p_td": 0.0, "a_td": 0.0, "p_fg": 0.0, "a_fg": 0.0})
            t["p_td"] += (pp.get("pass_td") or 0.0) + (pp.get("rush_td") or 0.0)
            t["a_td"] += (ss.get("pass_td") or 0.0) + (ss.get("rush_td") or 0.0)
            t["p_fg"] += pp.get("fgm") or 0.0
            t["a_fg"] += ss.get("fgm") or 0.0
            for k, floor in FLOORS:
                pv = pp.get(k) or 0.0
                if pv >= floor:
                    pl[k][0] += pv
                    pl[k][1] += ss.get(k) or 0.0
                    pl[k][2] += 1
        log(f"[trim] table: week {w}")
    tg = list(team.values())
    return {"team_games": len(tg),
            "td": sum(t["a_td"] for t in tg) / sum(t["p_td"] for t in tg),
            "fg": sum(t["a_fg"] for t in tg) / sum(t["p_fg"] for t in tg),
            "players": {k: {"n": n, "ratio": a / p} for k, (p, a, n) in pl.items()}}


def results(season, weeks, log=print):
    """[(home profile, away profile, home pts, away pts)] for the played games."""
    import kalshi
    import kalshi_nfl as K
    import nfl_data
    out = []
    for w in weeks:
        d = kalshi._get_json(f"{SB}?seasontype=2&week={w}&dates={season}")
        fin = {}
        for ev in d.get("events") or []:
            comp = (ev.get("competitions") or [{}])[0]
            if (((comp.get("status") or {}).get("type")) or {}).get("state") != "post":
                continue
            sc = {c.get("homeAway"): (K._canon((c.get("team") or {}).get("abbreviation")), float(c.get("score")))
                  for c in comp.get("competitors") or []}
            fin[(sc["home"][0], sc["away"][0])] = (sc["home"][1], sc["away"][1])
        for th, ta in nfl_data.week_games(str(season), w) or []:
            key = (K._canon(th["abbr"]), K._canon(ta["abbr"]))
            if key in fin:
                out.append((th, ta) + fin[key])
        log(f"[trim] results: week {w}")
    return out


def _slate_rates(pairs):
    import nfl_game_sim as G
    out = []
    for th, ta in pairs:
        lg = th.get("lg_pa_pg") or ta.get("lg_pa_pg")
        dh, da = G.def_factor(ta.get("def_pa_pg"), lg), G.def_factor(th.get("def_pa_pg"), lg)
        out.append((th, ta, dh, da))
    return out


def realized(slate, n=FIT_N, seed=SEED):
    """(realized TDs / projected TDs, realized points / nfl_data points) over
    the slate at the module's current trims, common random numbers."""
    import nfl_game_sim as G
    td_r = td_p = pt_r = pt_p = 0.0
    for gi, (th, ta, dh, da) in enumerate(slate):
        rh, ra = G._rates(th, th.get("home") is not False, dh), G._rates(ta, False, da)
        rng = random.Random(seed * 1000 + gi)
        for _ in range(n):
            s = G._play_game(rh, ra, rng)
            td_r += s[0]["td"] + s[1]["td"]
            pt_r += s[0]["pts"] + s[1]["pts"]
        for t in (th, ta):
            td_p += n * (t["exp"]["pass_td"] + t["exp"]["rush_td"])
            pt_p += n * t["points"]
    return td_r / td_p, pt_r / pt_p


def fit(slate, r_td, r_pts, log=print):
    import nfl_game_sim as G
    lo, hi = 0.5, 1.6
    for _ in range(22):
        G._TD_CAL = 0.5 * (lo + hi)
        lo, hi = (G._TD_CAL, hi) if realized(slate)[0] < r_td else (lo, G._TD_CAL)
    G._TD_CAL = round(0.5 * (lo + hi), 3)
    lo, hi = 0.5, 2.5
    for _ in range(22):
        G._FG_CAL = 0.5 * (lo + hi)
        lo, hi = (G._FG_CAL, hi) if realized(slate)[1] < r_pts else (lo, G._FG_CAL)
    G._FG_CAL = round(0.5 * (lo + hi), 3)
    got = realized(slate)
    log(f"[trim] fit: _TD_CAL {G._TD_CAL}, _FG_CAL {G._FG_CAL} -> TDs {got[0]:.4f} (target {r_td:.4f}), "
        f"points {got[1]:.4f} (target {r_pts:.4f})")
    return G._TD_CAL, G._FG_CAL, got


def _median(pts):
    pts = sorted(pts)
    for (l1, p1), (l2, p2) in zip(pts, pts[1:]):
        if p1 >= 0.5 >= p2 and p1 != p2:
            return l1 + (p1 - 0.5) * (l2 - l1) / (p1 - p2)
    return None


def vs_kalshi(pairs, n=3000):
    """{game: (sim median total, Kalshi de-vigged median)} on the believable
    rungs of each game's total ladder, at the module's current trims."""
    import kalshi_nfl as K
    import nfl_game_sim as G
    idx = K.index()
    out = {}
    for th, ta in pairs:
        suf = G._suffix_for(idx, th["abbr"], ta["abbr"])
        if not suf:
            continue
        g = idx.get(suf) or {}
        q = g.get("q") or {}
        mk = []
        for nline, side in sorted((g.get("total") or {}).items()):
            if not K.believable(q.get(("total", nline, True))):
                continue
            y, nn = side.get("over"), side.get("under")
            if y is not None and nn is not None and y + nn > 0:
                mk.append((nline - 0.5, y / (y + nn)))
        sim = G.simulate_game(th, ta, n=n, seed=SEED)
        ms = _median([(r["line"], r["over_pct"] / 100.0) for r in sim["total_ladder"]])
        out[f"{ta['abbr']}@{th['abbr']}"] = (ms, _median(mk))
    return out


def main(season=2026, last=6, fit_week=7, log=print):
    sys.path.insert(0, ROOT)
    import nfl_data
    import nfl_game_sim as G
    t0 = time.time()
    shipped = {"_TD_CAL": G._TD_CAL, "_FG_CAL": G._FG_CAL, "_PROJ_BIAS": dict(G._PROJ_BIAS)}
    weeks = range(1, last + 1)
    tab = table(season, weeks, log=log)
    games = results(season, weeks, log=log)
    proj = sum(th["points"] + ta["points"] for th, ta, _h, _a in games)
    act = sum(h + a for _th, _ta, h, a in games)
    d = [(h + a) - (th["points"] + ta["points"]) for th, ta, h, a in games]
    sd = math.sqrt(sum((x - sum(d) / len(d)) ** 2 for x in d) / (len(d) - 1))
    r_pts = act / proj
    pairs = nfl_data.week_games(str(season), fit_week) or []
    slate = _slate_rates(pairs)
    before = vs_kalshi(pairs)
    td_cal, fg_cal, got = fit(slate, tab["td"], r_pts, log=log)
    G._PROJ_BIAS = {k: round(v["ratio"], 3) for k, v in tab["players"].items()}
    after = vs_kalshi(pairs)
    both = [g for g in before if None not in before[g] and None not in after[g]]
    gap_b = sum(before[g][0] - before[g][1] for g in both) / max(1, len(both))
    gap_a = sum(after[g][0] - after[g][1] for g in both) / max(1, len(both))
    ships = bool(both) and abs(gap_a) <= SHIP_GAP and abs(gap_a) < abs(gap_b)
    out = {"season": season, "weeks": [1, last], "fit_week": fit_week, "shipped": shipped,
           "table": tab, "points": {"games": len(games), "actual": act / len(games), "projected": proj / len(games),
                                    "ratio": r_pts, "actual_minus_projected_se": sd / math.sqrt(len(d))},
           "refit": {"_TD_CAL": td_cal, "_FG_CAL": fg_cal, "_PROJ_BIAS": G._PROJ_BIAS,
                     "realized": {"td": got[0], "points": got[1]}},
           "vs_kalshi": {"games": both, "before": {g: before[g] for g in both}, "after": {g: after[g] for g in both},
                         "mean_gap_before": gap_b, "mean_gap_after": gap_a},
           "rule": {"max_abs_gap": SHIP_GAP}, "ships": ships,
           "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "secs": round(time.time() - t0, 1)}
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1)
    log(f"[trim] Kalshi gap before {gap_b:+.2f}, after {gap_a:+.2f} over {len(both)} games -> "
        f"{'SHIPS' if ships else 'does not ship'}; wrote {OUT}")
    return out


if __name__ == "__main__":
    sys.path.insert(0, ROOT)
    os.environ.setdefault("VIGIL_NO_BG", "1")
    a = [int(x) for x in sys.argv[1:]]
    main(*a)
