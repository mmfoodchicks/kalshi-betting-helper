"""Tables for the EXPERIMENTAL R5 comparison (research only): r5_compare.py's
game-level output and r5_dfs.py's DFS arm, every difference with its UNPAIRED
SE (the arms do not share their random numbers).

    python3 research/den_pit_audit/r5_report.py --r5 <r5.json> --dfs <r5dfs.npz> --out <json>
"""
import argparse
import json
import math

import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--r5", required=True)
ap.add_argument("--dfs", required=True)
ap.add_argument("--out", required=True)
a = ap.parse_args()
G = json.load(open(a.r5))
Z = np.load(a.dfs)
R = {"game": {}, "dfs": {}, "corr": {}, "captain_rank": {}}


def d(x, y):
    return {"prod": x["mean"], "r5": y["mean"], "diff": round(y["mean"] - x["mean"], 3),
            "se": round(math.sqrt(x["se"] ** 2 + y["se"] ** 2), 3), "prod_sd": x["sd"], "r5_sd": y["sd"],
            "prod_q": [x["p10"], x["p25"], x["p50"], x["p75"], x["p90"]],
            "r5_q": [y["p10"], y["p25"], y["p50"], y["p75"], y["p90"]]}


P, F = G["prod"], G["r5"]
for k in P["pts"]:
    R["game"]["pts_" + k] = d(P["pts"][k], F["pts"][k])
for k in P["close"]:
    x, y = P["close"][k], F["close"][k]
    R["game"]["share_" + k] = {"prod": x["p"], "r5": y["p"], "diff": round(y["p"] - x["p"], 4),
                               "se": round(math.sqrt(x["se"] ** 2 + y["se"] ** 2), 4)}
for side in (G["home"], G["away"]):
    for k in P[f"flow_{side}"]:
        R["game"][f"{side}_{k}"] = d(P[f"flow_{side}"][k], F[f"flow_{side}"][k])
    R["game"][f"{side}_dst"] = d(P["dst"][side], F["dst"][side])
    R["game"][f"{side}_k"] = d(P["k"][side], F["k"][side])
    R["game"][f"{side}_qb_pull"] = F.get(f"qb_pull_{side}")
R["r5_deal_usage"] = G.get("r5_usage")
R["r5_deal_players"] = G.get("r5_players")


def q(x, f):
    s = np.sort(x)
    return float(s[min(len(s) - 1, int(f * len(s)))])


def boot(x, f, B=200, seed=5):
    r = np.random.default_rng(seed)
    return float(np.std([q(x[r.integers(0, len(x), len(x))], f) for _ in range(B)], ddof=1))


names = sorted({k.split("|", 1)[1] for k in Z.files if not k.split("|", 1)[1].startswith("_")})
for nm in names:
    if f"prod|{nm}" not in Z.files or f"r5|{nm}" not in Z.files:
        continue
    x, y = Z[f"prod|{nm}"].astype(np.float64), Z[f"r5|{nm}"].astype(np.float64)
    if x.mean() < 0.5 and y.mean() < 0.5:
        continue
    R["dfs"][nm] = {"prod_mean": round(x.mean(), 3), "r5_mean": round(y.mean(), 3),
                    "d_mean": round(y.mean() - x.mean(), 3),
                    "se": round(math.sqrt(x.var(ddof=1) / len(x) + y.var(ddof=1) / len(y)), 3),
                    "prod_floor": q(x, 0.1), "r5_floor": q(y, 0.1), "prod_ceil": q(x, 0.9), "r5_ceil": q(y, 0.9),
                    "d_ceil_se": round(math.sqrt(boot(x, 0.9) ** 2 + boot(y, 0.9) ** 2), 3),
                    "prod_p99": q(x, 0.99), "r5_p99": q(y, 0.99)}
PAIRS = [("Bo Nix", "Jaylen Waddle"), ("Bo Nix", "Courtland Sutton"), ("Bo Nix", "Jonah Coleman"),
         ("Jonah Coleman", "DST_DEN"), ("Jonah Coleman", "K_DEN"), ("Jonah Coleman", "DST_PIT"),
         ("Bo Nix", "Mason Rudolph"), ("Jonah Coleman", "Jaylen Warren"), ("Mason Rudolph", "DK Metcalf"),
         ("Mason Rudolph", "Jaylen Warren"), ("Jaylen Warren", "DST_PIT"), ("Jaylen Warren", "DST_DEN"),
         ("Bo Nix", "DST_PIT"), ("Mason Rudolph", "DST_DEN"), ("K_DEN", "DST_DEN"), ("DST_DEN", "DST_PIT")]
for u, v in PAIRS:
    row = {}
    for arm in ("prod", "r5"):
        if f"{arm}|{u}" in Z.files and f"{arm}|{v}" in Z.files:
            x, y = Z[f"{arm}|{u}"], Z[f"{arm}|{v}"]
            r = float(np.corrcoef(x, y)[0, 1])
            row[arm] = {"r": round(r, 3), "se": round((1 - r * r) / math.sqrt(len(x) - 1), 3)}
    if len(row) == 2:
        row["diff"] = round(row["r5"]["r"] - row["prod"]["r"], 3)
        row["se"] = round(math.hypot(row["r5"]["se"], row["prod"]["se"]), 3)
    R["corr"][f"{u} ~ {v}"] = row
for arm in ("prod", "r5"):
    cap = []
    for nm in names:
        if nm.startswith(("DST_", "K_")) or f"{arm}|{nm}" not in Z.files:
            continue
        x = Z[f"{arm}|{nm}"].astype(np.float64)
        cap.append((nm, round(1.5 * x.mean(), 2), round(1.5 * q(x, 0.9), 2), round(1.5 * q(x, 0.99), 2)))
    R["captain_rank"][arm] = {"by_mean": [c[:2] for c in sorted(cap, key=lambda c: -c[1])[:10]],
                              "by_p90": [(c[0], c[2]) for c in sorted(cap, key=lambda c: -c[2])[:10]],
                              "by_p99": [(c[0], c[3]) for c in sorted(cap, key=lambda c: -c[3])[:10]]}
json.dump(R, open(a.out, "w"), indent=1, default=float)
print("DONE", a.out)
