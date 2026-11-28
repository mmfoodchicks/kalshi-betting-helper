"""Does the market anchor's 1.86 still hold with the fumble-return TD in the engine? (2026-11-28)

The two staged fixes flip together (research/reports/fumble_td_fix.md section E): the anchor's points per win
(research/deep_anchor_scale.py, K 1.86, verified at slope 1.0002 +/- 0.0064) was measured on the engine WITHOUT
fumble-return touchdowns. This re-runs that verification exactly -- the same 24 seasons, the same margins at
K 1.86, the same "verify" game seeds, n 300 -- with BOTH patches in: the anchor-patched nfl_deep_data (its
apply_anchor, ANCHOR_V2 on) building the levels and the fumble-patched nfl_deep_sim playing every game. The
stated tolerance is the anchor's own, unchanged: slope 1 +/- 0.03 with SE <= 0.01, win error at most the analytic
reference's + 0.10. Per season the slope is paired against the committed verification's (same seeds).

usage: VIGIL_NO_BG=1 python3 research/fumble_td_anchor_check.py [--n 300] [--procs 4]
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("VIGIL_NO_BG", "1")

OUT = os.path.join(ROOT, "research", "data", "fumble_td_anchor_check.json")


def main(argv):
    n = int(argv[argv.index("--n") + 1]) if "--n" in argv else 300
    procs = int(argv[argv.index("--procs") + 1]) if "--procs" in argv else 4
    from research import deep_anchor_patch as A
    from research import deep_anchor_scale as S
    from research import fumble_td_patch as F
    eng, data_mod = F.load(True), A.load(True)
    assert eng.FUMBLE_TD and eng.ENGINE_VERSION == 2 and data_mod.ANCHOR_V2
    saved = (sys.modules.get("nfl_deep_sim"), sys.modules.get("nfl_deep_data"))
    sys.modules["nfl_deep_sim"], sys.modules["nfl_deep_data"] = eng, data_mod   # forked workers inherit both
    try:
        data = json.load(open(S.SEASONS_PATH))["seasons"]
        ref = json.load(open(S.OUT))
        k = ref["k_ship"]
        ver = S.run_k(data, k, n, procs, "verify")
    finally:
        for name, mod in zip(("nfl_deep_sim", "nfl_deep_data"), saved):
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
    s_m, s_se = S.mean_se([x["slope"] for x in ver])
    mae_m, _ = S.mean_se([x["mae"] for x in ver])
    old = {x["season"]: x for x in ref["per_season"]["verify"]}
    d_m, d_se = S.mean_se([x["slope"] - old[x["season"]]["slope"] for x in ver])
    mae_an = ref["analytic_reference"]["mae_wins"]
    tol = ref["tolerance"]
    ok = tol["slope"][0] <= s_m <= tol["slope"][1] and s_se <= tol["se_max"] and mae_m <= mae_an + tol["mae_allow"]
    out = {"k": k, "n_per_game": n, "seeds": "deep_anchor_scale's 'verify' tag (the committed verification's)",
           "engine": {"nfl_deep_sim": "fumble-TD patched (ENGINE_VERSION 2)", "nfl_deep_data": "anchor-patched (ANCHOR_V2)"},
           "verify": {"slope": round(s_m, 4), "se": round(s_se, 4), "mae_wins": round(mae_m, 4)},
           "committed_verify": ref["verify"],
           "paired_slope_change": {"mean": round(d_m, 4), "se": round(d_se, 4)},
           "tolerance": tol, "analytic_mae": mae_an, "holds": ok, "per_season": ver}
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print(json.dumps({k_: v for k_, v in out.items() if k_ != "per_season"}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
