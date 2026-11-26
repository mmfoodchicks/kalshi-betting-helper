"""Markdown tables for report.md, from conc.json (no hand transcription)."""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
r = json.load(open(os.path.join(HERE, "conc.json")))
P = []
def w(s=""): P.append(s)

# A1: Thanksgiving MEGA, named players across lists
b = r["156932"]; cs = b["contests"]["197897297"]
names = ["Rome Odunze", "Sam LaPorta", "Emmett Johnson", "Luther Burden III", "Caleb Williams", "Javonte Williams", "Amon-Ra St. Brown", "Jahmyr Gibbs"]
lists = ["top_money@8(shown)", "top_money@20", "top_top1@20", "top_win@20", "top_ev@20", "portfolio"]
pl = {p["name"]: p for p in json.load(open(os.path.join(HERE, "data", "tourney-156932.json")))["players"]}
w("| player | field_pct | opt_pct | " + " | ".join(lists) + " |")
w("|---|---|---|" + "---|" * len(lists))
for n in names:
    row = []
    for l in lists:
        tp = {p["name"]: p["exp_pct"] for p in cs["lists"][l]["top_players"]}
        # top_players holds the 12 most used; recompute if absent
        row.append(f"{tp.get(n, None):.0f}" if n in tp else "<=")
    w(f"| {n} | {pl[n]['field_pct']} | {pl[n]['opt_pct']} | " + " | ".join(row) + " |")
w()
w("| measure | " + " | ".join(lists) + " |")
w("|---|" + "---|" * len(lists))
def g(l, f): return f(cs["lists"][l])
w("| Caleb Williams at QB % | " + " | ".join(f"{cs['lists'][l]['qb'].get('Caleb Williams', 0):.0f}" for l in lists) + " |")
w("| QBs used | " + " | ".join(", ".join(f"{k.split()[-1]} {v:.0f}" for k, v in cs['lists'][l]['qb'].items()) for l in lists) + " |")
w("| Caleb + Burden + Odunze stack % | " + " | ".join(f"{next((s['pct'] for s in cs['lists'][l]['top_stacks'] if s['stack']==['Caleb Williams','Luther Burden III','Rome Odunze']), 0):.0f}" for l in lists) + " |")
w("| CHI@DET share of roster slots % (field 39.8, opt 40.4) | " + " | ".join(f"{cs['lists'][l]['game_slot_share'].get('CHI@DET', 0):.1f}" for l in lists) + " |")
w("| lineups with 4+ from CHI@DET % | " + " | ".join(f"{cs['lists'][l]['game_ge4_pct'].get('CHI@DET', 0):.0f}" for l in lists) + " |")
w("| distinct players used | " + " | ".join(str(cs['lists'][l]['players_used']) for l in lists) + " |")
w("| mean shared players per pair (of 9) | " + " | ".join(str(cs['lists'][l]['shared']['mean']) for l in lists) + " |")
w("| max shared (8 = a one-player swap) | " + " | ".join(str(cs['lists'][l]['shared']['max']) for l in lists) + " |")
w("| pairs sharing 7-8 players % | " + " | ".join(f"{100*sum(v for k,v in cs['lists'][l]['shared']['dist'].items() if int(k)>=7)/cs['lists'][l]['shared']['pairs']:.0f}" for l in lists) + " |")
w()
# A2: list overlap
w("Lineups shared between lists (of 20), MEGA: " + ", ".join(f"{k.replace('@20','')} {v}" for k, v in cs["overlap"]["pairwise_shared_lineups"].items() if "portfolio" in k or k.startswith("top_money")))
w()
# A3: classic summary all contests
w("| board / contest | list | distinct players | mean shared | top exposures (exp% / field%) | top QB |")
w("|---|---|---|---|---|---|")
for dg in ("156932", "157066"):
    for cid, cs in r[dg]["contests"].items():
        for l in ("portfolio", "top_money@20", "top_top1@20"):
            v = cs["lists"][l]
            tops = "; ".join(f"{p['name']} {p['exp_pct']:.0f}/{p['field_pct']}" for p in v["top_players"][:3])
            q = next(iter(v["qb"].items()))
            w(f"| {dg} {cs['contest'][4:34]} | {l.replace('@20','')} | {v['players_used']} | {v['shared']['mean']} | {tops} | {q[0]} {q[1]:.0f} |")
w()
# A4: showdown
w("| board | list | captains used | top captain % (field CPT %) | most-used player % | team split 5-1/4-2/3-3 | mean shared (of 6) | same-captain pairs % |")
w("|---|---|---|---|---|---|---|---|")
for dg in ("157074", "157075", "157072", "157086"):
    bb = r[dg]
    items = [(k.replace("@20", ""), v) for k, v in bb["lists"].items() if k in ("top_money@20", "top_top1@20")] + [("portfolio 20", bb["portfolio"]["20"])]
    for k, v in items:
        c0 = next(iter(v["captains"].items()))
        fc = next((p["field_cpt_pct"] for p in v["top_players"] if p["name"] == c0[0]), "?")
        tp = v["top_players"][0]
        sp = v["team_split_pct"]
        w(f"| {dg} {bb['contest'].split('(')[-1].rstrip(')')} | {k} | {v['n_captains']} | {c0[0]} {c0[1]:.0f} ({fc}) | {tp['name']} {tp['exp_pct']:.0f} | {sp.get('5-1',0):.0f}/{sp.get('4-2',0):.0f}/{sp.get('3-3',0):.0f} | {v['shared']['mean']} | {v['same_captain_pair_pct']} |")
open(os.path.join(HERE, "tables.md"), "w").write("\n".join(P))
print("\n".join(P))
