"""Cut the raw ESPN summaries (research/nba_matchup_fetch.py) into the matchup
engine's season states -- the SAME shape production's nightly job
(nba_matchup_data.update) keeps in the deep store, so the research fit and the
server read identical rows (RESEARCH ONLY; the raw files never enter git).

  python3 research/nba_matchup_build.py <raw dir> --seasons 2021,2022,2023,2024,2025,2026

Writes deep-store key nba_matchup_<season> under $DEEP_CACHE_DIR and prints
the quality gate's counts (games cut, gate failures, the stint minutes
against the box's).
"""
import argparse
import glob
import gzip
import json
import multiprocessing as mp
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _cut(path):
    import nba_matchup_data as D
    import nba_pbp as P
    try:
        with gzip.open(path, "rt") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None, "read"
    rec = D.parse_game(d)
    if not rec:
        return None, "parse"
    if P.points_check(rec) != (rec["home_score"], rec["away_score"]):
        return None, "points"
    e = D.game_entry(rec)
    if not D.entry_ok(e):
        return None, "gate"
    return e, "ok"


def build(raw, season, workers=4, log=print):
    import deep_cache
    import nba_matchup_data as D
    import nba_pbp as P
    d = os.path.join(raw, str(season))
    with open(os.path.join(d, "schedule.json")) as fh:
        events = {e["id"]: e for e in json.load(fh)}
    paths = sorted(glob.glob(os.path.join(d, "*.json.gz")))
    t0 = time.time()
    games, mismatch, counts = {}, [], {}
    with mp.Pool(workers) as pool:
        for path, (e, why) in zip(paths, pool.imap(_cut, paths, chunksize=8)):
            gid = os.path.basename(path).split(".")[0]
            counts[why] = counts.get(why, 0) + 1
            ev = events.get(gid)
            if e is None or not ev or not P.league_game(ev):
                if e is None:
                    mismatch.append(gid)
                continue
            e["date"] = ev.get("date") or e["date"]
            games[gid] = e
    st = {"schema": D.SCHEMA, "season": int(season), "games": games, "events": events, "mismatch": mismatch,
          "through": None, "updated": time.time(), "source": "research/nba_matchup_build.py (raw ESPN summaries)"}
    deep_cache.save(D.STATE_KEY.format(season=int(season)), st)
    log(f"[build] {season}: {len(games)} games kept, {counts}, {time.time() - t0:.0f}s")
    return st


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("raw")
    ap.add_argument("--seasons", default="2021,2022,2023,2024,2025,2026")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    for s in [int(x) for x in a.seasons.split(",")]:
        build(a.raw, s, workers=a.workers)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
