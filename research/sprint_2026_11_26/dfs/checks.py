"""Portfolio consistency on the exported boards: the served p_any curve must
behave like a union on shared worlds (first point = entry 1's own top-1%,
non-decreasing, each step <= that entry's own top-1%, total <= the sum),
and whether a board's contests share one portfolio."""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
out = {}
def check(p_any, top1):
    steps = [p_any[0]] + [round(b - a, 2) for a, b in zip(p_any, p_any[1:])]
    return {"first_eq_entry1": abs(p_any[0] - top1[0]) <= 0.06,
            "nondecreasing": all(b >= a - 1e-9 for a, b in zip(p_any, p_any[1:])),
            "step_le_own": all(s <= t + 0.06 for s, t in zip(steps, top1)),
            "le_sum": p_any[-1] <= sum(top1) + 0.1,
            "sum_top1": round(sum(top1), 1), "p_any_last": p_any[-1],
            "additive_overstatement_x": round(sum(top1) / p_any[-1], 2)}
for dg in (156932, 157066):
    d = json.load(open(os.path.join(HERE, "data", f"tourney-{dg}.json")))
    ports = {}
    for cid, r in d["results"].items():
        pf = r["portfolio"]
        ports[cid] = [frozenset(e["names"]) for e in pf["entries"]]
        out[f"{dg}/{cid}"] = check(pf["p_any_top1_pct"], [e["top1_pct"] for e in pf["entries"]])
    ids = list(ports)
    out[f"{dg}/same_portfolio_across_contests"] = {f"{a}~{b}": (len(set(ports[a]) & set(ports[b])), ports[a] == ports[b]) for i, a in enumerate(ids) for b in ids[i+1:]}
for dg in (157074, 157075, 157072, 157086):
    d = json.load(open(os.path.join(HERE, "data", f"tourney-{dg}.json")))
    e20 = d["portfolio"]["20"]["entries"]
    t = [e["top1_pct"] for e in e20]
    # p_any only at 5, 10, 20 on showdown
    pts = {k: d["portfolio"][k]["p_any_top1_pct"] for k in ("5", "10", "20")}
    prefix_ok = all(d["portfolio"][k]["entries"] == e20[:int(k)] for k in ("5", "10"))
    out[str(dg)] = {"p_any": pts, "sum_top1_20": round(sum(t), 1), "additive_overstatement_x": round(sum(t) / pts["20"], 2),
                    "entry1_top1": t[0], "prefix_nested": prefix_ok,
                    "basis": d["portfolio"]["20"].get("p_any_basis", {}).get("in_sample")}
json.dump(out, open(os.path.join(HERE, "checks.json"), "w"), indent=1)
print(json.dumps(out, indent=1))
