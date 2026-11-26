import sys, os
os.environ.setdefault("VIGIL_NO_BG", "1")
os.environ.setdefault("ERRLOG_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "errlog.db"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))  # the repo root
import numpy as np
import nfl_dfs_engine as G

def recv_pool(k, tdr_decay):
    pl = [{"name": "Q", "pos": "QB", "team": "A", "means": {"pass_att": 40, "pass_cmp": 30, "pass_yd": 300, "pass_td": 3, "int": 0.5, "rush_att": 2, "rush_yd": 5}}]
    for j in range(k):
        rec = 2.0
        pl.append({"name": f"R{j}", "pos": "WR", "team": "A", "means": {"rec_tgt": 2.5, "rec": rec, "rec_yd": 20, "rec_td": rec * max(1e-9, tdr_decay ** j), "rush_att": 0}})
    return pl

n = 2000
for k, comp in ((14, 14), (20, 20), (30, 30), (40, 60)):
    pl = recv_pool(k, 0.05)
    box = {"pts": np.zeros(n, int), "pass_att": np.full(n, comp + 5), "comp": np.full(n, comp), "pass_yds": np.full(n, comp * 10),
           "td_pass": np.full(n, comp), "int": np.zeros(n, int), "rush_att": np.full(n, 10), "rush_yds": np.full(n, 40), "td_rush": np.full(n, 10),
           "fum_lost": np.zeros(n, int), "kneels": np.zeros(n, int)}
    d = G.deal_team(pl, list(range(len(pl))), box, np.random.default_rng(1))
    rtd = sum(d[i]["rec_td"] for i in range(1, len(pl)))
    ru = sum(d[i].get("rush_td", 0) for i in range(len(pl)))
    print(f"k={k} comp=td_pass={comp}: QB pass_td==box {bool(np.array_equal(d[0]['pass_td'], box['td_pass']))}  sum rec_td==td_pass in {(rtd == comp).mean()*100:.1f}% of worlds, TDs lost {int((comp - rtd).sum())}; rush_td conserved {bool(np.array_equal(ru, box['td_rush']))}")
