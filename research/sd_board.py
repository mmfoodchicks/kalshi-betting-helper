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
#: the two pinned Showdown draft groups (S6.0 capture); the CLI default
PINNED_DGS = (153086, 153085)
#: Sleeper's roster / depth chart, captured beside the projection feeds. The
#: depth-chart gate (nfl_dfs._apply_depth -> nfl_adp.consensus) read LIVE
#: Sleeper in every research process until 2026-09-18, when the football A/B's
#: first DEN @ KC arm built on 24 players and 777,056 legal lineups and the next
#: three on 23 and 583,565: Marvin Mims Jr. was listed Out between the two
#: processes, eight minutes apart. A fence that is not pinned is not a fence.
ROSTER_FILE = "players_{season}_{week}.json"
#: the only fields nfl_adp.consensus reads, so the capture stays small enough
#: to commit (the live blob is ~12 MB for every player in the league)
_ROSTER_FIELDS = ("position", "full_name", "search_rank", "injury_status", "team",
                  "years_exp", "status", "depth_chart_position", "depth_chart_order", "active")


def roster_path(feed_dir, season=SEASON, week=WEEK):
    return os.path.join(feed_dir, ROSTER_FILE.format(season=season, week=week))


def feeds(feed_dir, season=SEASON, week=WEEK):
    """Pin the Sleeper projection feeds AND the Sleeper roster to captured
    copies so every arm of a study sees byte-identical inputs even if Sleeper
    moves mid-study. Refuses to run without the roster capture: a study that
    silently reads the live depth chart is the fence break of 2026-09-18."""
    import json
    import nfl_adp
    import nfl_dfs_sim as S
    raw = json.load(open(os.path.join(feed_dir, f"proj_{season}_{week}.json")))
    dfn = json.load(open(os.path.join(feed_dir, f"proj_{season}_{week}_def.json")))
    kck = json.load(open(os.path.join(feed_dir, f"proj_{season}_{week}_k.json")))
    S._get = lambda url: (dfn if "DEF" in url else kck if "position[]=K" in url else raw)
    path = roster_path(feed_dir, season, week)
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path}: no pinned roster; capture one with "
                                "`python3 -m research.sd_board capture-roster` before any study runs")
    cap = json.load(open(path))
    # the pin is a STATE, not a patched fetch: nfl_adp.roster() answers from it
    # before any cache or network is consulted, and stamps the source "pinned"
    nfl_adp._PINNED = (nfl_adp._build(cap["players"]),
                       {"file": os.path.relpath(path, ROOT),
                        "captured_utc": (cap.get("meta") or {}).get("captured_utc")})
    return S


def roster_stamp(feed_dir, season=SEASON, week=WEEK):
    """What roster a study ran on: the file, its sha256, when it was captured."""
    import hashlib
    import json
    path = roster_path(feed_dir, season, week)
    blob = open(path, "rb").read()
    meta = json.loads(blob).get("meta") or {}
    return {"file": os.path.relpath(path, ROOT), "sha256": hashlib.sha256(blob).hexdigest(),
            "captured_utc": meta.get("captured_utc"), "records": meta.get("records"),
            "teams": meta.get("teams")}


def capture_roster(feed_dir, dgs=PINNED_DGS, season=SEASON, week=WEEK, log=print):
    """Capture the Sleeper roster records the depth-chart gate can consult for
    the pinned draft groups: every record on the slates' teams plus every
    record whose normalised name matches a slate player (a man DraftKings lists
    on one team and Sleeper on another still has to be found). Only the fields
    nfl_adp.consensus reads are kept."""
    import datetime
    import hashlib
    import json
    import nfl_adp
    import nfl_dfs
    import simulate
    from research import s6_capture
    teams, names = set(), set()
    for dg in dgs:
        slate, _ = s6_capture.replay(int(dg))
        if not slate:
            raise SystemExit(f"no pinned slate for draft group {dg}")
        for e in nfl_dfs.showdown_pool(simulate.parse_dk_csv(slate["csv"])):
            if e.get("team"):
                teams.add(e["team"])
            names.add(nfl_adp._norm(e["name"]))
    live = nfl_adp._fetch_players()
    kept = {}
    for pid, p in live.items():
        if p.get("position") not in nfl_adp._POS:
            continue
        if (p.get("team") or "") in teams or nfl_adp._norm(p.get("full_name") or "") in names:
            kept[pid] = {k: p.get(k) for k in _ROSTER_FIELDS}
    out = {"meta": {"captured_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "source": nfl_adp._URL, "season": season, "week": week,
                    "draft_groups": [int(d) for d in dgs], "teams": sorted(teams),
                    "records": len(kept), "fields": list(_ROSTER_FIELDS),
                    "why": ("the depth-chart gate is an input to the legal universe; unpinned, it "
                            "moved between two arms of the football A/B on 2026-09-18")},
           "players": kept}
    path = roster_path(feed_dir, season, week)
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    log(f"[roster] {len(kept)} Sleeper records for {sorted(teams)} -> {os.path.relpath(path, ROOT)} "
        f"sha256 {hashlib.sha256(open(path, 'rb').read()).hexdigest()[:12]}")
    return out


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
            season=SEASON, log=print, model="legacy"):
    """The board's player entries, with the pool built under `discrete`.

    `model` names the game simulator ("legacy" or "constrained"); it exists so
    the Showdown legacy-vs-constrained structural A/B can swap ONLY the
    football model and keep everything downstream identical. Under
    "constrained" the `discrete` flag is ignored by player_pool (that model is
    integer-valued by construction) and `seed` seeds the worlds themselves.

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
                                  teams=teams, discrete=discrete, model=model,
                                  seed=(int(seed) if (seed is not None and model == "constrained") else None)) or {}
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
    C = T.contest_capacity(contest)
    if C is None:
        # research fails loud: a contest without maximumEntries used to be
        # modelled on its CURRENT fill (1.4% of capacity at the S6 capture)
        raise ValueError(f"contest {contest.get('id')} carries no maximumEntries; "
                         "refusing to size the field on the current fill")
    grid = T.payout_grid(C, contest.get("payouts") or [],
                        float(contest.get("entry_fee") or 1.0),
                        int(contest.get("places_paid") or max(1, C // 5)))
    return grid, C


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "capture-roster":
        capture_roster(os.path.join(ROOT, "research", "data", "feeds"),
                       tuple(int(x) for x in a[1:]) or PINNED_DGS)
    else:
        print("usage: python3 -m research.sd_board capture-roster [dg ...]")
