"""What the deep-anchor fix moves downstream, BEFORE it is deployed (2026-11-27; research/reports/deep_anchor_fix.md).

The fix is staged as research/data/deep_anchor_flip.patch (nfl_deep_data.py is frozen until the registered 100-game
deep-weight read). This script builds the same production inputs both ways from the patched module. "v1" is the
switch inside it off (2.25), "v2" on (MARKET_PTS_PER_WIN_V2). Every random draw is shared between the two (common seeds), so each
delta is the anchor's alone.

No outcome of any registered window is read. Results before a week are inputs, as production reads them; no
projection here is scored.

Weeks:
- current: 2026 week 12's games not yet final, the live Kalshi ladder, every game played so far;
- past: 2026 week 9, with the play-by-play store cut before week 9 and the ladder rebuilt from Kalshi's daily
  candlesticks (the YES ask's close on the last day before week 9's first kickoff, the price production
  read).

Measured, both ways:
  games    the deep engine on the slate's profiles (production's own build_profiles with the switch, Sleeper's
           roster factors folded in as nfl_game_sim does): home win, margin and total mean / SD / quantiles,
           blowout rates (|margin| >= 17, >= 24), 4,000 games each
  season   nfl_deep_season.run_deep from that week (1,000 seasons, one seed): division and playoff
           probabilities, expected wins
  dfs      nfl_dfs_sim.player_pool on the engine model at 10,000 worlds, one seed: mean DK points by
           position, the same-team QB-WR1/WR2 and RB1-DST correlations
  board    (current week, the main classic slate) dfs_tourney.build_nfl_classic at reduced size on one seed:
           each list's top-N overlap and Spearman rank correlation between the two builds

usage: VIGIL_NO_BG=1 python3 research/deep_anchor_downstream.py [--skip-board] -> research/data/deep_anchor_downstream.json
       VIGIL_NO_BG=1 python3 research/deep_anchor_downstream.py --noise   # the board's seed-to-seed yardstick, added to it
"""
import copy
import json
import math
import os
import random
import sys
import time
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("VIGIL_NO_BG", "1")

import nfl_deep_data as D                           # noqa: E402
import nfl_pbp as P                                 # noqa: E402
import nfl_season                                   # noqa: E402

OUT = os.path.join(ROOT, "research", "data", "deep_anchor_downstream.json")
SEASON = 2026
N_GAMES, N_SEASONS, N_WORLDS = 4000, 1000, 10000


def store_before(store, w):
    """The play-by-play store as it stood before week w: games of earlier weeks, every later event not final."""
    games = {gid: r for gid, r in store["games"].items() if r.get("st") != 2 or int(r.get("week") or 99) < w}
    events = []
    for e in store["events"]:
        e = dict(e)
        if e.get("st") == 2 and int(e.get("week") or 99) >= w:
            e["state"], e["home_pts"], e["away_pts"] = "pre", None, None
        events.append(e)
    return dict(store, games=games, events=events)


def ladder_at(end_ts):
    """{abbr: E[W]} off Kalshi's win-total ladder as it stood at end_ts: production's own _market_wins with
    every market's cents the YES ask's close on the last daily candle ending at or before end_ts."""
    import kalshi
    import pro_prices
    import racing
    rows, cursor = [], ""
    for _ in range(10):
        url = f"{kalshi.BASE}/markets?series_ticker=KXNFLWINS&limit=1000" + (f"&cursor={cursor}" if cursor else "")
        d = kalshi._get_json(url)
        rows.extend(d.get("markets") or [])
        cursor = d.get("cursor") or ""
        if not cursor:
            break
    live = {m["event_ticker"].split("-")[1][:2] for m in rows if m.get("status") in ("active", "open")}
    rows = [m for m in rows if m["event_ticker"].split("-")[1][:2] in live]
    hist = []
    for m in rows:
        url = (f"{kalshi.BASE}/series/KXNFLWINS/markets/{m['ticker']}/candlesticks?start_ts={end_ts - 4 * 86400}"
               f"&end_ts={end_ts}&period_interval=1440")
        try:
            cs = [c for c in (kalshi._get_json(url).get("candlesticks") or []) if c["end_period_ts"] <= end_ts]
        except Exception:
            cs = []
        ask = ((cs[-1].get("yes_ask") or {}).get("close_dollars")) if cs else None
        hist.append({"title": (m.get("title") or "").lower(),
                     "sub": (m.get("yes_sub_title") or m.get("subtitle") or "").lower(),
                     "cents": kalshi._cents(ask) if ask is not None else None})
        time.sleep(0.06)
    orig = pro_prices._markets
    try:
        pro_prices._markets = lambda st: hist if st == "KXNFLWINS" else orig(st)
        racing._form_cache.pop(("nflss_mkt_wins",), None)
        out = nfl_season._market_wins()
    finally:
        pro_prices._markets = orig
        racing._form_cache.pop(("nflss_mkt_wins",), None)
    return out, len(hist), sum(1 for h in hist if h["cents"] is not None)


def build(store, ladder, v2):
    """Production's build_profiles from the PATCHED nfl_deep_data (research/data/deep_anchor_flip.patch), the
    switch set to v2, the ladder fixed. nfl_deep_data.py itself is frozen until the deep-weight read; this
    result was first produced while the same code sat in the file (commit 8aed79e, switch toggled), and the
    patch is that code byte for byte with the switch on."""
    from research import deep_anchor_patch as Pt
    mod = Pt.load(v2)
    orig_mw = nfl_season._market_wins
    try:
        nfl_season._market_wins = lambda: dict(ladder)
        profs, _q = mod.build_profiles(SEASON, store=store)
    finally:
        nfl_season._market_wins = orig_mw
    return profs


_G = {}


def _init(profs):
    import nfl_deep_sim as E
    _G["T"], _G["P"] = E.tables(), profs


def _game(spec):
    import nfl_deep_sim as E
    h, a, n, s, neutral = spec
    rng = random.Random(s)
    out = []
    for _ in range(n):
        g = E.play_game(_G["P"][h], _G["P"][a], rng, _G["T"], neutral=neutral)
        out.append((g["h"]["pts"], g["a"]["pts"]))
    return out


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def game_dists(profs, games, tag):
    import multiprocessing as mp
    specs = [(g["home"], g["away"], N_GAMES, zlib.crc32(f"{tag}|{g['id']}".encode()), bool(g.get("neutral")))
             for g in games]
    with mp.get_context("fork").Pool(4, initializer=_init, initargs=(profs,)) as pool:
        res = pool.map(_game, specs)
    rows, allm, allt = [], [], []
    for g, r in zip(games, res):
        m = [h - a for h, a in r]
        t = [h + a for h, a in r]
        allm += m
        allt += t
        rows.append({"game": f"{g['away']} @ {g['home']}", "p_home": sum(x > 0 for x in m) / len(m),
                     "margin": sum(m) / len(m), "total": sum(t) / len(t)})
    sd = lambda xs: math.sqrt(sum((x - sum(xs) / len(xs)) ** 2 for x in xs) / len(xs))
    return {"games": rows,
            "pooled": {"abs_margin_mean": sum(abs(x) for x in allm) / len(allm), "margin_sd": sd(allm),
                       "total_mean": sum(allt) / len(allt), "total_sd": sd(allt),
                       "total_q10_50_90": [q(allt, .1), q(allt, .5), q(allt, .9)],
                       "abs_margin_q50_90": [q([abs(x) for x in allm], .5), q([abs(x) for x in allm], .9)],
                       "blowout_17": sum(abs(x) >= 17 for x in allm) / len(allm),
                       "blowout_24": sum(abs(x) >= 24 for x in allm) / len(allm)}}


def season_probs(profs, store):
    import nfl_deep_season as S
    agg = S.run_deep(SEASON, n_seasons=N_SEASONS, workers=4, seed=20261127, profiles=copy.deepcopy(profs),
                     store=store, track_progress=False)
    n = agg["n"]
    return {ab: {"division": agg["division"].get(ab, 0) / n, "playoffs": agg["playoffs"].get(ab, 0) / n,
                 "wins": agg["wins_sum"].get(ab, 0) / n} for ab in nfl_season._DIV_OF}


def _with_profiles(profs, week, fn):
    """Run fn with nfl_game_sim's deep profiles fixed to `profs` (roster factors folded in, as production)."""
    import nfl_game_sim as G
    import nfl_dfs_sim as NS
    factors = D.roster_factors(SEASON, week)
    pr = D.with_roster(profs, factors) if factors else profs
    orig = G._deep_profiles
    try:
        G._deep_profiles = lambda season, wk: pr
        NS._cache.clear()
        return fn()
    finally:
        G._deep_profiles = orig
        NS._cache.clear()


def corr(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    return sxy / (sx * sy) if sx and sy else 0.0


def dfs_stats(pool):
    by_pos, qbwr, rbdst = {}, [], []
    teams = {}
    for name, p in pool.items():
        arr = [float(v) for v in p["arr"]]
        p["_mean"] = sum(arr) / len(arr)
        p["_arr"] = arr
        by_pos.setdefault(p["pos"], []).append(p["_mean"])
        teams.setdefault(p["team"], []).append(p)
    for t, ps in teams.items():
        rank = lambda pos: sorted([p for p in ps if p["pos"] == pos], key=lambda p: -(p.get("proj") or 0))
        qb, wr, rb, dst = rank("QB"), rank("WR"), rank("RB"), rank("DST") or rank("DEF")
        if qb:
            for w in wr[:2]:
                qbwr.append(corr(qb[0]["_arr"], w["_arr"]))
        if rb and dst:
            rbdst.append(corr(rb[0]["_arr"], dst[0]["_arr"]))
    return {"mean_by_pos": {k: sum(v) / len(v) for k, v in sorted(by_pos.items())},
            "qb_wr_corr": sum(qbwr) / len(qbwr) if qbwr else None, "qb_wr_pairs": len(qbwr),
            "rb1_dst_corr": sum(rbdst) / len(rbdst) if rbdst else None,
            "player_mean": {n: p["_mean"] for n, p in pool.items()}}


def dfs_pool(profs, week):
    import nfl_dfs_sim as NS
    return _with_profiles(profs, week, lambda: NS.player_pool(week, n=N_WORLDS, season=str(SEASON), model="engine",
                                                              seed=20261127, workers=4))


def board(profs, week, dg):
    import dfs_tourney as T
    return _with_profiles(profs, week, lambda: T.build_nfl_classic(
        dg, n_sims=20000, opt_worlds=8000, field_n=100000, cand_n=40000, cal_n=10000, candidates=600, top=40,
        k_port=20, week=week, seed=20261127, model="engine", workers=4, log=lambda *a: None))


def board_lists(art):
    """{contest:list: [lineup as its sorted player names]} for every ranked list a classic board carries."""
    out = {}
    for cid, res in ((art or {}).get("results") or {}).items():
        for lname, lst in res.items():
            if lname.startswith("top_") and isinstance(lst, list):
                out[f"{cid}:{lname}"] = [tuple(sorted(r.get("names") or [])) for r in lst]
    return out


def spearman_overlap(a, b, n):
    sa, sb = a[:n], b[:n]
    overlap = len(set(sa) & set(sb)) / max(1, n)
    common = [x for x in sa if x in set(b)]
    if len(common) < 3:
        return overlap, None
    ra = {x: i for i, x in enumerate(a)}
    rb = {x: i for i, x in enumerate(b)}
    return overlap, corr([ra[x] for x in common], [rb[x] for x in common])


def summarize(v1, v2):
    """Deltas v2 - v1 for the headline figures."""
    d = {}
    for k in ("abs_margin_mean", "margin_sd", "total_mean", "total_sd", "blowout_17", "blowout_24"):
        d[k] = v2["games"]["pooled"][k] - v1["games"]["pooled"][k]
    gp = [abs(a["p_home"] - b["p_home"]) for a, b in zip(v1["games"]["games"], v2["games"]["games"])]
    d["game_p_home_abs_delta_max"] = max(gp) if gp else None
    d["game_p_home_abs_delta_mean"] = sum(gp) / len(gp) if gp else None
    for k in ("division", "playoffs"):
        dl = [abs(v2["season"][ab][k] - v1["season"][ab][k]) for ab in v1["season"]]
        d[f"season_{k}_abs_delta_mean"] = sum(dl) / len(dl)
        d[f"season_{k}_abs_delta_max"] = max(dl)
    if v1.get("dfs") and v2.get("dfs"):
        d["dfs_mean_by_pos"] = {p: v2["dfs"]["mean_by_pos"][p] - v1["dfs"]["mean_by_pos"][p]
                                for p in v1["dfs"]["mean_by_pos"] if p in v2["dfs"]["mean_by_pos"]}
        pm = [abs(v2["dfs"]["player_mean"][n] - v1["dfs"]["player_mean"][n]) for n in v1["dfs"]["player_mean"]
              if n in v2["dfs"]["player_mean"]]
        d["dfs_player_mean_abs_delta_mean"] = sum(pm) / len(pm)
        d["dfs_player_mean_abs_delta_max"] = max(pm)
        d["qb_wr_corr"] = v2["dfs"]["qb_wr_corr"] - v1["dfs"]["qb_wr_corr"]
        d["rb1_dst_corr"] = (v2["dfs"]["rb1_dst_corr"] or 0) - (v1["dfs"]["rb1_dst_corr"] or 0)
    return d


def one_week(label, store, ladder, week, games, do_dfs=True):
    res = {}
    for v2 in (False, True):
        tag = "v2" if v2 else "v1"
        profs = build(store, ladder, v2)
        import nfl_game_sim as G
        r = {"anchor_version": G.anchor_version(profs),
             "pts_per_win": next(p["anchor"]["pts_per_win"] for p in profs.values() if p.get("anchor")),
             "games": game_dists(D.with_roster(profs, D.roster_factors(SEASON, week) or {}), games, label),
             "season": season_probs(profs, store)}
        if do_dfs:
            try:
                r["dfs"] = dfs_stats(dfs_pool(profs, week))
            except Exception as e:
                r["dfs_error"] = f"{type(e).__name__}: {e}"
        res[tag] = r
        res[tag + "_profiles"] = profs
        print(f"[{label}] {tag} done", flush=True)
    return res


def noise_baseline():
    """The board's own seed-to-seed spread: v1 built twice on the current week, seeds 20261127 and 20261128,
    so the v1-vs-v2 overlaps have a yardstick. Added to the committed result."""
    import dfs_tourney as T
    full = P.season_records(SEASON)
    sched = P.regular_schedule(full, set(nfl_season._DIV_OF))
    w = min(g["week"] for g in sched if not g["final"])
    profs = build(full, nfl_season._market_wins(), False)
    d = json.load(open(OUT))
    dg = d["board"]["dg"]

    def b(seed):
        return _with_profiles(profs, w, lambda: T.build_nfl_classic(
            dg, n_sims=20000, opt_worlds=8000, field_n=100000, cand_n=40000, cal_n=10000, candidates=600, top=40,
            k_port=20, week=w, seed=seed, model="engine", workers=4, log=lambda *a: None))
    l1, l2 = board_lists(b(20261127)), board_lists(b(20261128))
    out = {}
    for k in l1:
        if k in l2:
            ov, rho = spearman_overlap(l1[k], l2[k], 20)
            out[k] = {"top20_overlap": ov, "spearman_common": rho, "n": len(l1[k])}
    d["board"]["noise_v1_seed_vs_seed"] = out
    with open(OUT, "w") as fh:
        json.dump(d, fh, indent=1, sort_keys=True, default=str)
    print(json.dumps(out, indent=0))


def main(argv):
    if "--noise" in argv:
        return noise_baseline()
    skip_board = "--skip-board" in argv
    full = P.season_records(SEASON)
    sched = P.regular_schedule(full, set(nfl_season._DIV_OF))
    from research import deep_anchor_patch as Pt
    out = {"season": SEASON, "k_v1": nfl_season._PTS_PER_WIN, "k_v2": Pt.load(True).MARKET_PTS_PER_WIN_V2,
           "weeks": {}}
    # current: week 12's games not yet final, the live ladder
    cur_w = min(g["week"] for g in sched if not g["final"])
    cur_games = [g for g in sched if g["week"] == cur_w and not g["final"]]
    live = nfl_season._market_wins()
    cur = one_week("current", full, live, cur_w, cur_games)
    # past: week 9, the store cut before it, the ladder off the candlesticks
    past_w = 9
    sub = store_before(full, past_w)
    kick = min(g for g in [e.get("date") for e in full["events"] if e.get("st") == 2
                           and int(e.get("week") or 0) == past_w] if g) if any(
        e.get("date") for e in full["events"]) else None
    import datetime
    end_ts = int(datetime.datetime.strptime(kick[:16], "%Y-%m-%dT%H:%M").replace(
        tzinfo=datetime.timezone.utc).timestamp()) - 3600 if kick else None
    ladder, n_mk, n_priced = ladder_at(end_ts)
    past_games = [g for g in P.regular_schedule(sub, set(nfl_season._DIV_OF)) if g["week"] == past_w]
    past = one_week("past", sub, ladder, past_w, past_games)
    for label, w, r, extra in (("current", cur_w, cur, {"ladder": "live", "ladder_teams": len(live)}),
                               ("past", past_w, past, {"ladder": f"candlesticks at {end_ts} ({n_priced}/{n_mk} priced)", "ladder_teams": len(ladder),
                                                       "first_kickoff": kick})):
        out["weeks"][label] = dict(extra, week=w, games=len(r["v1"]["games"]["games"]),
                                   v1={k: v for k, v in r["v1"].items() if k != "dfs"},
                                   v2={k: v for k, v in r["v2"].items() if k != "dfs"},
                                   dfs_v1={k: v for k, v in (r["v1"].get("dfs") or {}).items() if k != "player_mean"},
                                   dfs_v2={k: v for k, v in (r["v2"].get("dfs") or {}).items() if k != "player_mean"},
                                   delta=summarize(r["v1"], r["v2"]))
    if not skip_board:
        import dk
        main_dg = None
        m = dk.main_slate(dk.slates("nfl")) or {}
        main_dg = m.get("dg") or m.get("draft_group_id")
        if main_dg:
            arts = {tag: board(cur[tag + "_profiles"], cur_w, main_dg) for tag in ("v1", "v2")}
            l1, l2 = board_lists(arts["v1"]), board_lists(arts["v2"])
            out["board"] = {"dg": main_dg, "lists": {}}
            for k in l1:
                if k in l2:
                    ov, rho = spearman_overlap(l1[k], l2[k], 20)
                    out["board"]["lists"][k] = {"top20_overlap": ov, "spearman_common": rho, "n": len(l1[k])}
        else:
            out["board"] = {"skipped": "no main slate found"}
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True, default=str)
    print(json.dumps({w: v["delta"] for w, v in out["weeks"].items()}, indent=1, default=str))
    print(json.dumps(out.get("board"), indent=1, default=str)[:2000])


if __name__ == "__main__":
    main(sys.argv[1:])
