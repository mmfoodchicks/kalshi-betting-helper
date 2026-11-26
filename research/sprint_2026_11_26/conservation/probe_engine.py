import sys, os, time, random
os.environ.setdefault("VIGIL_NO_BG", "1")
os.environ.setdefault("ERRLOG_DB", os.path.join(__import__("tempfile").gettempdir(), "conservation_probe_errlog.db"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))  # the repo root
import numpy as np
import nfl_deep_sim as E, nfl_dfs_engine as G
T = E.tables(); L = E.league_profile(T)
t0 = time.time()
bh, ba = G.play_worlds(L, dict(L, abbr="B"), 3000, random.Random(4), T, env_sd=0.14)
print("3000 games", round(time.time() - t0, 1), "s")
for nm, b in (("h", bh), ("a", ba)):
    print(nm, "plays==att+sacks+rush+kneels", bool(np.array_equal(b["plays"], b["pass_att"] + b["sacks"] + b["rush_att"] + b["kneels"])),
          "td_pass<=comp", bool((b["td_pass"] <= b["comp"]).all()), "td_rush<=rush_att", bool((b["td_rush"] <= b["rush_att"]).all()),
          "comp<=att", bool((b["comp"] <= b["pass_att"]).all()), "max td_pass", int(b["td_pass"].max()), "max td_rush", int(b["td_rush"].max()),
          "max kneels", int(b["kneels"].max()), "min pass_yds", int(b["pass_yds"].min()), "min rush_yds", int(b["rush_yds"].min()),
          "fg tiers", bool(np.array_equal(b["fg_made"], b["fg_0_39"] + b["fg_40_49"] + b["fg_50p"])),
          "ret_pts>=6td_ret", bool((b["ret_pts"] >= 6 * b["td_ret"]).all()), "ret_pts<=8td_ret", bool((b["ret_pts"] <= 8 * b["td_ret"]).all()))
