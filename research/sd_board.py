"""The showdown board's prelude, reusable, with the simulator mode as a knob.

`dfs_tourney.build_nfl_showdown` deliberately takes no `discrete` argument --
that is the isolation that keeps the experimental scorer out of production. But
the promotion question cannot be answered without running the SAME board both
ways, so the prelude is rebuilt here out of the SAME functions the production
builder calls: `dk.slate_for`, `simulate.parse_dk_csv`, `nfl_dfs.showdown_pool`,
`nfl_dfs._pool_match`, `nfl_dfs._apply_depth`, `dfs_tourney.enumerate_showdown`,
`dfs_tourney.field_weights`, `dfs_tourney.payout_grid`.

Nothing here is imported by the app, the PC worker or the guard suite's
production paths. If this file and `build_nfl_showdown` ever disagree about how
a board is assembled, this file is the one that is wrong.
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SEASON, WEEK = "2026", 1


def feeds(feed_dir, season=SEASON, week=WEEK):
    """Pin the Sleeper projection feeds to a captured copy so the two modes
    see byte-identical inputs even if Sleeper moves mid-study."""
    import json
    import nfl_dfs_sim as S
    raw = json.load(open(os.path.join(feed_dir, f"proj_{season}_{week}.json")))
    dfn = json.load(open(os.path.join(feed_dir, f"proj_{season}_{week}_def.json")))
    kck = json.load(open(os.path.join(feed_dir, f"proj_{season}_{week}_k.json")))
    S._get = lambda url: (dfn if "DEF" in url else kck if "position[]=K" in url else raw)
    return S


def slate_and_contest(dg, contest_id=None):
    """The live draft group and its richest GPP, exactly as the builder picks
    them."""
    import dk
    slate = dk.slate_for("nfl", draft_group_id=int(dg))
    if not slate:
        return None, None
    contests = slate.get("contests") or []
    contest = dk.contest_detail(int(contest_id)) if contest_id else None
    if not contest and contests:
        gpp = [c for c in contests if (c.get("entries") or 0) >= 100]
        big = max(gpp or contests, key=lambda c: (c.get("prize_pool") or 0))
        contest = dk.contest_detail(int(big["id"]))
    return slate, contest


def ents_for(slate, week, discrete, n_sims=12000, seed=None, preseason=False,
            season=SEASON, log=print):
    """The board's player entries, with the pool built under `discrete`.

    Returns (ents, seconds, peak_rss_mb). `seed` seeds the legacy module RNG
    first, so the two modes start from the same generator state -- they do NOT
    consume it identically (the discrete mode draws calibration passes), so
    this removes run-to-run luck as an explanation for a difference without
    pretending the draws are paired.
    """
    import resource
    import nfl_dfs
    import nfl_dfs_sim
    import simulate
    csv_players = simulate.parse_dk_csv(slate["csv"])
    ents = nfl_dfs.showdown_pool(csv_players)
    teams = {e.get("team") for e in ents if e.get("team")}
    if seed is not None:
        nfl_dfs_sim._random.seed(int(seed))
    t0 = time.time()
    pool = nfl_dfs_sim.player_pool(week, n=int(n_sims), preseason=preseason, season=season,
                                  teams=teams, discrete=discrete) or {}
    secs = time.time() - t0
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    nidx, norm = nfl_dfs._norm_index(pool)
    for e in ents:
        sim = nfl_dfs._pool_match(pool, e["name"], e["pos"], e.get("team"), nidx, norm)
        if sim and sim.get("arr"):
            e["proj"], e["ceiling"], e["floor"], e["arr"] = (
                round(sim["proj"], 1), sim["ceiling"], sim["floor"], sim["arr"])
        else:
            e["_drop"] = True
    ents = [e for e in ents if not e.get("_drop")]
    by_name = {e["name"]: e for e in ents}
    ents, dx = nfl_dfs._apply_depth(ents, preseason)
    import dfs_tourney as T
    extra = []
    for d in dx:
        e = by_name.get(d["name"])
        if e is not None and "depth chart" in (d.get("why") or "") \
                and (e.get("proj") or 0) >= T._FIELD_EXTRA_MIN_PROJ:
            e["_field_only"] = True
            e["depth"] = (d["why"].split(" ") or [""])[0]
            extra.append(e)
    extra.sort(key=lambda e: -e["proj"])
    ents = ents + extra[:T._FIELD_EXTRA_MAX]
    log(f"[board] {'discrete' if discrete else 'legacy  '} pool {len(ents)} players "
        f"in {secs:.0f}s, peak RSS {rss:,.0f} MB")
    return ents, secs, rss


def universe(ents):
    """(idx, W, allowed) over every legal lineup, under the production rules."""
    import dfs_tourney as T
    import nfl_dfs

    def entry_ok(cap_p, picked):
        got = []
        for p in picked:
            if p.get("_field_only") or not nfl_dfs._sd_allowed(p, cap_p, got):
                return False
            got.append(p)
        return True

    rich = sum(1 for p in ents if p.get("pos") in nfl_dfs._SD_GPP_CPT_POS
               and not p.get("_field_only")
               and p["cpt_salary"] >= nfl_dfs._SD_MIN_CPT_SALARY) >= 3

    def cpt_ok(p):
        if p.get("_field_only") or p.get("pos") not in nfl_dfs._SD_GPP_CPT_POS:
            return False
        return (not rich) or p["cpt_salary"] >= nfl_dfs._SD_MIN_CPT_SALARY

    return T.enumerate_showdown(ents, nfl_dfs.CAP, cpt_mult=nfl_dfs.CPT_MULT,
                               entry_ok=entry_ok, cpt_ok=cpt_ok)


def grid_for(contest):
    """The payout grid and the field cap the builder would use."""
    import dfs_tourney as T
    C = int(contest.get("max_entries") or contest.get("entered") or 0) or 10000
    grid = T.payout_grid(C, contest.get("payouts") or [],
                        float(contest.get("entry_fee") or 1.0),
                        int(contest.get("places_paid") or max(1, C // 5)))
    return grid, C
