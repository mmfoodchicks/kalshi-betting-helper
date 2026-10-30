"""Shared compute artifacts — the contract between the server and the PC worker.

Everything heavy this app computes lands as a flat pickle file in one of three
stores, each read by freshness (mtime) wherever it is consumed:

  gamesim  {pk}.pkl               per-game MLB sims (combo maker, warm loop)
  boards   {name}.pkl             every boardshare board (NFL sims/boards, golf,
                                  LoL, NBA, NHL, tennis, UFC, futures, kalshi idx)
  deep     {key}.pkl              deep_cache: the nightly 4,000-season run
                                  ("mlb_deep"), day-history records, futures sims

That uniformity is the whole offload design: the PC runs the SAME builder
functions against its own local stores, then uploads any file fresher than the
server's copy. Adoption is automatic — boardshare.get, deep_cache.load and
_sim_disk_get already prefer the freshest file on disk, and deep_cache's
nightly scheduler judges "already ran today" from the saved file's timestamp,
so a PC-uploaded run makes the server skip its own multi-hour rebuild with no
scheduler changes at all.

SCHEMA is the version gate for the whole contract: the server refuses uploads
whose schema differs, so a stale (or ahead-of-deploy) PC checkout is ignored,
never adopted. BUMP IT in the same commit as any change to what these pickles
contain.
"""

import os
import re

# 2: the UFC board carries per-sample end-round/method arrays (ufc_combo).
# 3: sd_tourney_nfl_<draft group> boards (dfs_tourney, PC-built).
# 4: the deep store gains nfl_deep.pkl (nfl_deep_season.run_deep's aggregate)
#    and nfl_pbp_<season>.pkl (nfl_pbp's compact play-by-play season store).
# 5: the deep store's soccer_rows_<year> entries carry each league's shots
#    ((rows, names, shots), soccer.history) and soccer_model carries shots_w.
# 6: the deep store gains nba_matchup_<season>.pkl (the NBA matchup engine's cut
#    player-games), nba_matchup_book_<season>.pkl (its as-of replay) and the
#    nba_matchup job marker; the boards store gains matchups_<sport>_<date>.pkl
#    (the Matchups tab's slate index) and matchups_<sport>_<date>_g<id>.pkl (one
#    game's full players), matchups.py; and nfl_matchup_<season>.pkl (the NFL
#    matchup data's compact play store, nfl_matchup_data SCHEMA 2) with
#    nfl_matchup_ids.pkl (its player-id crosswalk) and the nfl_matchup marker;
#    and nfl_matchup_defhist_<season>_wNN.pkl (nfl_matchup.def_history: one
#    week's defense vectors, ~2.7 KB, written once for a week the committed
#    table lacks, never rewritten).
# 7: the deep store gains nfl_rush_aux_<season>.pkl (the rushing layer's
#    auxiliary store, nfl_rush_data SCHEMA 2: pbp extras aligned to
#    nfl_matchup_<season>'s rows, situational on-field counts, PFR rush / def /
#    pass, NGS weekly rushing, injuries, snap counts, block coverage), built
#    beside the matchup store by the nfl_matchup job (matchups.nfl_data_job),
#    and its once-a-week retry marker nfl_rush_backfill_try_<season>.pkl.
# 8: the boards store gains nflview_<season>_wNN_<AWAY>_<HOME>.pkl (nfl_viewer:
#    one game's typical and ten other simulated games, play by play, names
#    drawn, the per-down table), and nfl_deep_sim's sack clamp changes what
#    the deep engine plays (a sack that nets yards stops short of the goal).
SCHEMA = 8

_NAME_RE = re.compile(r"^[\w.,@=+-]{1,140}\.pkl$")


def dir_for(kind):
    """The store directory for an artifact kind, honoring the same env vars the
    consumers read. None for an unknown kind."""
    if kind == "gamesim":
        import baseball
        return baseball._SIM_DISK
    if kind == "boards":
        import boardshare
        return boardshare._DIR
    if kind == "deep":
        import deep_cache
        return deep_cache.CACHE_DIR
    return None


def valid_name(name):
    """A bare flat filename — no separators, no traversal, bounded length."""
    return bool(name) and bool(_NAME_RE.match(name)) and "/" not in name and "\\" not in name


def ages(kind):
    """{filename: age_seconds} for every artifact in a store."""
    import time
    d = dir_for(kind)
    out = {}
    if not d or not os.path.isdir(d):
        return out
    now = time.time()
    for name in os.listdir(d):
        if not valid_name(name):
            continue
        try:
            out[name] = round(now - os.stat(os.path.join(d, name)).st_mtime, 1)
        except OSError:
            continue
    return out


def write_raw(kind, name, data):
    """Adopt an externally-computed artifact: atomic temp+rename into the store
    the consumers already read. Caller has authenticated and schema-checked."""
    import tempfile
    d = dir_for(kind)
    if not d or not valid_name(name):
        raise ValueError("bad kind/name")
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, os.path.join(d, name))
