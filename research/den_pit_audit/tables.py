"""Markdown tables for research/reports/den_pit_showdown_audit.md from the audit's
JSON outputs and the three boards (research only).

    python3 research/den_pit_audit/tables.py --head <dir> --pre <dir> --night <board.json>
        --refresh <board.json> --data research/data/den_pit_audit > tables.md
"""
import argparse
import json
import os

ap = argparse.ArgumentParser()
for k in ("head", "pre", "night", "refresh", "data"):
    ap.add_argument("--" + k, required=True)
a = ap.parse_args()
bh = json.load(open(os.path.join(a.head, "board.json")))
bp = json.load(open(os.path.join(a.pre, "board.json")))
bn = json.load(open(a.night))
br = json.load(open(a.refresh))
C = json.load(open(os.path.join(a.data, "compare.json")))
U = json.load(open(os.path.join(a.data, "universe.json"))) if os.path.exists(os.path.join(a.data, "universe.json")) else None


def p(*cells):
    print("| " + " | ".join(str(c) for c in cells) + " |")


print("### T1 players: served night, served refresh, HEAD rebuild (descriptive; inputs differ)\n")
p("player", "pos", "night proj / floor / ceil / optCPT", "refresh proj / floor / ceil / optCPT",
  "HEAD rebuild proj / floor / ceil / optCPT")
p(*["---"] * 5)
n = {x["name"]: x for x in bn["players"]}
r = {x["name"]: x for x in br["players"]}
h = {x["name"]: x for x in bh["players"]}
for nm in sorted(set(n) | set(r) | set(h), key=lambda k: -(h.get(k) or r.get(k) or n.get(k))["proj"]):
    def c(d):
        x = d.get(nm)
        return "-" if x is None else f"{x['proj']} / {x['floor']} / {x['ceiling']} / {x['opt_cpt_pct']}"
    pos = (h.get(nm) or r.get(nm) or n.get(nm))["pos"]
    p(nm, pos, c(n), c(r), c(h))

print("\n### T2 pre-sprint vs HEAD, same inputs and seed: every pool player's worlds\n")
p("player", "pos", "mean pre", "mean HEAD", "diff (paired SE)", "floor pre/HEAD", "ceiling pre/HEAD",
  "worlds moved of 60,000")
p(*["---"] * 8)
for x in sorted(C["players"], key=lambda x: -x["mean_head"]):
    p(x["name"] + (" (field only)" if x["field_only"] else ""), x["pos"], x["mean_pre"], x["mean_head"],
      f"{x['d_mean']:+.4f} ({x['d_mean_paired_se']:.4f})", f"{x['floor_pre']:g} / {x['floor_head']:g}",
      f"{x['ceil_pre']:g} / {x['ceil_head']:g}", x["worlds_moved"])

print("\n### T3 the board's lists, pre vs HEAD (leaders, the boards' own figures)\n")
p("list", "same top 10 order", "top-10 overlap", "top-20 overlap", "all-60 overlap")
p(*["---"] * 5)
for k, v in C["lists"].items():
    p(k, v["same_order_top10"], v["overlap_top10"], v["overlap_top20"], v["overlap_all"])

if U:
    print("\n### T4 tracked lineups, pre vs HEAD with PAIRED Monte Carlo SE (percent)\n")
    p("lineup (CPT + FLEX)", "labels", "cash pre -> HEAD (d, SE)", "top 1%", "top 0.1%", "first or share",
      "sole first", "EV $")
    p(*["---"] * 8)
    keep = {f"head:{ln}:{k}" for ln in ("top_money", "top_top1") for k in range(1, 6)} | \
        {f"head:top_win:{k}" for k in range(1, 4)} | {"head:chalk"}
    for t in [t for t in U["tracked"] if keep & set(t["labels"])]:
        def f(k, s=100, dp=3):
            return (f"{s * t[k + '_pre']:.{dp}f} -> {s * t[k + '_head']:.{dp}f} "
                    f"({s * t[k + '_d']:+.{dp}f}, {s * t[k + '_d_paired_se']:.{dp}f})")
        p(f"{t['captain']} + {', '.join(t['flex'])}", "; ".join(t["labels"][:3]), f("cash", dp=2), f("top1", dp=3),
          f("top01", dp=4), f("first_or_share", dp=4), f("sole_first", dp=5), f("ev", 1, 2))
