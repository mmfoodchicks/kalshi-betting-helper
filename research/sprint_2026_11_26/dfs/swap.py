"""Question B: the board's own figures for Javonte Williams + Luther Burden III
against Jahmyr Gibbs + Kalif Raymond, on the unpicked Thanksgiving board
(dg 156932). Every listed row (all lists, the portfolio, every contest)."""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(HERE, "data", "tourney-156932.json")))
A = {"Javonte Williams", "Luther Burden III"}
B = {"Jahmyr Gibbs", "Kalif Raymond"}
cname = {str(c["id"]): c["name"] for c in d["contests"]}
rows = {}   # (cid, frozenset) -> (row, [list@rank])
for cid, r in d["results"].items():
    for lst in ("top_money", "top_top1", "top_top01", "top_win", "top_ev"):
        for i, rw in enumerate(r[lst]):
            k = (cid, frozenset(rw["names"]))
            rows.setdefault(k, [rw, []])[1].append(f"{lst}#{i+1}")
    for i, rw in enumerate(r["portfolio"]["entries"]):
        k = (cid, frozenset(rw["names"]))
        rows.setdefault(k, [rw, []])[1].append(f"portfolio#{i+1}")
    if r.get("chalk"):
        k = (cid, frozenset(r["chalk"]["names"]))
        rows.setdefault(k, [r["chalk"], []])[1].append("chalk")
cols = ("salary", "proj", "top1_pct", "top01_pct", "win_pct", "money_pct", "cash_pct", "ev", "roi_pct", "opt_pct", "expected_copies")
out = {"pairs": [], "best": {}}
for cid in d["results"]:
    rs = [(k[1], v) for k, v in rows.items() if k[0] == cid]
    withA = [(s, v) for s, v in rs if A <= s]
    withB = [(s, v) for s, v in rs if B <= s]
    withG = [(s, v) for s, v in rs if "Jahmyr Gibbs" in s]
    for s, v in withA:
        core = s - A
        for s2, v2 in withB:
            if s2 - B == core:
                out["pairs"].append({"contest": cname[cid], "core": sorted(core),
                                     "A": {c: v[0].get(c) for c in cols} | {"where": v[1]},
                                     "B": {c: v2[0].get(c) for c in cols} | {"where": v2[1]}})
    def best(lst, key):
        lst = sorted(lst, key=lambda x: -x[1][0][key])
        return [{"names": sorted(x[0]), **{c: x[1][0].get(c) for c in cols}, "where": x[1][1]} for x in lst[:3]]
    out["best"][cname[cid]] = {"n_rows": len(rs), "n_withA": len(withA), "n_withB": len(withB), "n_withGibbs": len(withG),
                               "A_by_top1": best(withA, "top1_pct"), "B_by_top1": best(withB, "top1_pct"),
                               "Gibbs_by_top1": best(withG, "top1_pct")}
json.dump(out, open(os.path.join(HERE, "swap.json"), "w"), indent=1)
print(len(out["pairs"]), "exact swap pairs", file=sys.stderr)
