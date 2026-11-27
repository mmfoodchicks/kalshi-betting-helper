"""Record one NFL showdown's PRE-LOCK inputs for the eligibility comparison (2026-11-27; the registered read
research/data/dfs_eligibility/prereg.json reads only slates recorded by this before their lock).

    python3 research/dfs_eligibility_record.py <draft group id> <out dir>

Refuses once the slate has locked: a recording made after kickoff could carry in-game information (a status, a
projection, a box score) into a number that claims to be pre-game. It writes, in <out dir>:
  board.json      the gate's answer as the production builder computes it (dfs_tourney.build_nfl_showdown's first
                  half, the same functions: the DraftKings pool, the week, the projection match, nfl_dfs._apply_depth
                  on the live roster, dfs_tourney._field_extras); lineups_legal / lineups_allowed are left None (the
                  harness fills them; a production board for the same slate, when one is on sim-history, is the check)
  pool_engine.pkl 60,000 worlds from production's simulator (board_model, seed = the draft group id)
  dk_players.json, contest.json, sleeper_proj.json.gz, usage_hist.json.gz, roster.pkl, meta.json
Then: python3 research/dfs_eligibility_compare.py <out dir>.
"""
import gzip
import json
import os
import pickle
import sys
import time

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
os.environ.setdefault("VIGIL_NO_BG", "1")

N_WORLDS = 60000


def record(dg, out, workers=3):
    import numpy as np
    import dfs_tourney as T
    import dk
    import nfl_adp
    import nfl_dfs
    import nfl_dfs_sim
    import nfl_game_sim
    import nfl_preseason
    import nfl_usage
    import simulate
    os.makedirs(out, exist_ok=True)
    slate = dk.slate_for("nfl", draft_group_id=int(dg))
    if not slate:
        raise SystemExit("DraftKings has no pool for this draft group")
    lock = nfl_dfs.iso_ts(slate.get("starts"))
    if lock is None or lock <= time.time():
        raise SystemExit("the slate has locked (or its start is unreadable): a recording now would not be pre-game")
    contests = slate.get("contests") or []
    gpp = [c for c in contests if (c.get("entries") or 0) >= 100]
    big = max(gpp or contests, key=lambda c: (c.get("prize_pool") or 0))
    contest = dk.contest_detail(int(big["id"]))
    csv_players = simulate.parse_dk_csv(slate["csv"])
    ents = nfl_dfs.showdown_pool(csv_players)
    teams = {e.get("team") for e in ents if e.get("team")}
    preseason = nfl_preseason.is_preseason()
    week = nfl_game_sim.current_week(preseason, day=T._slate_day(slate))
    model = T.board_model(None, preseason)
    pool = nfl_dfs_sim.player_pool(week, n=N_WORLDS, preseason=preseason, teams=teams, model=model, seed=int(dg),
                                   workers=workers, compact=True) or {}
    nidx, norm = nfl_dfs._norm_index(pool)
    excluded = []
    for e in ents:
        sim = nfl_dfs._pool_match(pool, e["name"], e["pos"], e.get("team"), nidx, norm)
        if sim and T._has_arr(sim):
            e["proj"], e["arr"] = round(sim["proj"], 1), sim["arr"]
        else:
            excluded.append({"name": e["name"], "pos": e["pos"], "team": e.get("team"), "why": "no projection this week"})
            e["_drop"] = True
    ents = [e for e in ents if not e.get("_drop")]
    by_name = {e["name"]: e for e in ents}
    kept, dx = nfl_dfs._apply_depth(ents, preseason, float(lock) - time.time())
    extra = T._field_extras(by_name, dx)
    rows = []
    for e in kept + extra:
        rows.append({"name": e["name"], "pos": e["pos"], "team": e["team"], "salary": int(e["salary"]),
                     "cpt_salary": int(e["cpt_salary"]), "proj": e["proj"], "depth": e.get("depth"),
                     "field_only": bool(e.get("_field_only"))})
    board = {"draft_group_id": int(dg), "kind": "showdown", "built_ts": int(time.time()), "kickoff_ts": int(lock),
             "players": rows, "excluded": excluded + dx, "lineups_legal": None, "lineups_allowed": None,
             "field_model": {"beta": None}, "slate": {"teams": sorted(teams), "week": week},
             "recorded_by": "research/dfs_eligibility_record.py", "roster_source": nfl_dfs.roster_state()}
    json.dump(board, open(os.path.join(out, "board.json"), "w"), indent=1)
    slim = {e["name"]: {"proj": e["proj"], "arr": np.asarray(e["arr"])} for e in kept + extra}
    for d in dx:                       # the gated players the field does not play: their worlds too
        if d["name"] in by_name and d["name"] not in slim:
            slim[d["name"]] = {"proj": by_name[d["name"]]["proj"], "arr": np.asarray(by_name[d["name"]]["arr"])}
    pickle.dump(slim, open(os.path.join(out, "pool_engine.pkl"), "wb"))
    json.dump(dk.players(int(dg)), open(os.path.join(out, "dk_players.json"), "w"))
    json.dump(contest, open(os.path.join(out, "contest.json"), "w"))
    q = "&".join(f"position[]={p}" for p in ("QB", "RB", "WR", "TE"))
    url = f"{nfl_dfs_sim._PROJ.format(season=nfl_dfs_sim._season(), week=week)}?season_type=regular&{q}&order_by=pts_ppr"
    json.dump({"ts": time.time(), "url": url, "rows": nfl_dfs_sim._get(url)},
              gzip.open(os.path.join(out, "sleeper_proj.json.gz"), "wt"))
    json.dump({"ts": time.time(), "hist": nfl_usage.history(nfl_dfs_sim._season(), week)},
              gzip.open(os.path.join(out, "usage_hist.json.gz"), "wt"))
    recs, st = nfl_adp.roster()
    pickle.dump({"recs": recs, "state": st, "ts": time.time()}, open(os.path.join(out, "roster.pkl"), "wb"))
    meta = {"dg": int(dg), "contest": int(contest["id"]), "week": week, "season": nfl_dfs_sim._season(),
            "seed": int(dg), "model": model, "worlds": N_WORLDS, "recorded_ts": int(time.time()), "lock_ts": int(lock),
            "recorded_before_lock": True}
    json.dump(meta, open(os.path.join(out, "meta.json"), "w"), indent=1)
    return meta


if __name__ == "__main__":
    print(json.dumps(record(int(sys.argv[1]), sys.argv[2])))
