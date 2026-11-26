import sys, os, random
os.environ.setdefault("VIGIL_NO_BG", "1")
os.environ.setdefault("ERRLOG_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "errlog.db"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))  # the repo root
import numpy as np
import nfl_deep_sim as E, nfl_dfs_engine as G
T = E.tables(); L = E.league_profile(T)
bh, ba = G.play_worlds(L, dict(L, abbr="B"), 2000, random.Random(3), T, env_sd=0.14)
pl = [{"name": "Q", "pos": "QB", "team": "A", "means": {"pass_att": 34, "pass_cmp": 22, "pass_yd": 240, "pass_td": 1.6, "int": 0.7, "rush_att": 3, "rush_yd": 14, "rush_td": 0.1, "fum": 0.15}},
      {"name": "R1", "pos": "RB", "team": "A", "means": {"rush_att": 16, "rush_yd": 70, "rush_td": 0.5, "rec_tgt": 4, "rec": 3, "rec_yd": 22, "rec_td": 0.1, "fum": 0.1}},
      {"name": "R2", "pos": "RB", "team": "A", "means": {"rush_att": 7, "rush_yd": 30, "rush_td": 0.2, "rec_tgt": 2, "rec": 1.5, "rec_yd": 10, "rec_td": 0.05}},
      {"name": "W1", "pos": "WR", "team": "A", "means": {"rec_tgt": 9, "rec": 6, "rec_yd": 80, "rec_td": 0.5, "fum": 0.05}},
      {"name": "W2", "pos": "WR", "team": "A", "means": {"rec_tgt": 7, "rec": 4.5, "rec_yd": 55, "rec_td": 0.35}},
      {"name": "T1", "pos": "TE", "team": "A", "means": {"rec_tgt": 5, "rec": 3.6, "rec_yd": 38, "rec_td": 0.3, "fum": 0.02}}]
d = G.deal_team(pl, list(range(6)), bh, np.random.default_rng(2))
tot = no_touch = 0
for i in range(6):
    f = d[i].get("fum", np.zeros(2000, int))
    touches = d[i].get("rec", 0) + d[i].get("rush_att", 0) + (d[i].get("pass_att", 0) + bh["sacks"] if i == 0 else 0)
    tot += int(f.sum()); no_touch += int(f[np.asarray(touches) == 0].sum())
print("dealt lost fumbles", tot, "on a player with no touch in that world", no_touch, f"({100*no_touch/max(tot,1):.1f}%)")
