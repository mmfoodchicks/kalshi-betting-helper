"""Why the rebuilt board's drive-model level is empty (research only): replay
nfl_dfs_engine.week_context's drive step on the recorded inputs and print what
nfl_data.week_games returns and any error it ledgers."""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

out = sys.argv[1]
common.setup(out, sys.argv[2], "replay", sys.argv[3] if len(sys.argv) > 3 else None)
import errlog  # noqa: E402

notes = []
_n = errlog.note
errlog.note = lambda *a, **k: (notes.append((a, k)), _n(*a, **k))[1]
import nfl_data  # noqa: E402
import nfl_game_sim  # noqa: E402

try:
    g = nfl_data.week_games(str(common.SEASON), common.WEEK)
    print("week_games:", None if g is None else len(g))
    for h, a in (g or [])[:20]:
        print("  ", a.get("abbr"), "@", h.get("abbr"))
    for h, a in g or []:
        if {h.get("abbr"), a.get("abbr")} == {"PIT", "DEN"}:
            sim = nfl_game_sim.simulate_game(h, a, n=2400, seed=1)
            print("drive DEN/PIT:", sim.get("exp_home"), sim.get("exp_away"))
except Exception:
    traceback.print_exc()
for a_, k in notes:
    print("NOTE", [str(x)[:300] for x in a_], {kk: str(v)[:200] for kk, v in k.items()})
