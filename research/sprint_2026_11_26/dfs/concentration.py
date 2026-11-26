"""Portfolio concentration on the exported tournament boards (backlog 3.6).

Read-only: reads the board JSONs exported from origin/sim-history into
data/, writes conc.json beside this script. Every figure is a count over the
board's own listed lineups; nothing is re-simulated.
"""
import itertools
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")


def load(dg):
    return json.load(open(os.path.join(DATA, f"tourney-{dg}.json")))


def pairs_shared(sets):
    if len(sets) < 2:
        return None
    sh = [len(a & b) for a, b in itertools.combinations(sets, 2)]
    c = Counter(sh)
    return {"mean": round(sum(sh) / len(sh), 2), "max": max(sh), "min": min(sh),
            "pairs": len(sh), "dist": {str(k): c[k] for k in sorted(c)}}


def classic_list_stats(rows, players, n=None):
    rows = rows[:n] if n else rows
    by = {p["name"]: p for p in players}
    L = len(rows)
    sets = [frozenset(r["names"]) for r in rows]
    distinct = len(set(sets))
    pc = Counter(nm for s in sets for nm in s)
    qb = Counter()
    team_slots = Counter()
    game_slots = Counter()
    game_ge4 = Counter()
    stack = Counter()
    bring = 0
    stack_keys = Counter()
    for r in rows:
        lu = r["lineup"]
        q = [p for p in lu if p["pos"] == "QB"][0]
        qb[q["name"]] += 1
        qteam = q["team"]
        qopp = by[q["name"]].get("opp")
        mates = [p["name"] for p in lu if p["team"] == qteam and p["pos"] in ("WR", "TE")]
        stack[len(mates)] += 1
        bb = [p["name"] for p in lu if p["team"] == qopp and p["pos"] != "DST"]
        bring += bool(bb)
        stack_keys[(q["name"],) + tuple(sorted(mates))] += 1
        g = Counter()
        for p in lu:
            team_slots[p["team"]] += 1
            gm = by[p["name"]].get("game")
            g[gm] += 1
            game_slots[gm] += 1
        for gm, k in g.items():
            if k >= 4:
                game_ge4[gm] += 1
    top_players = []
    for nm, k in pc.most_common(12):
        p = by.get(nm, {})
        top_players.append({"name": nm, "pos": p.get("pos"), "team": p.get("team"), "salary": p.get("salary"),
                            "proj": p.get("proj"), "exp_pct": round(100.0 * k / L, 1),
                            "field_pct": p.get("field_pct"), "opt_pct": p.get("opt_pct"),
                            "casual_pct": p.get("casual_pct")})
    return {"n": L, "distinct": distinct,
            "players_used": len(pc),
            "top_players": top_players,
            "qb": {k: round(100.0 * v / L, 1) for k, v in qb.most_common()},
            "team_slot_share": {k: round(100.0 * v / (9 * L), 1) for k, v in team_slots.most_common(8)},
            "game_slot_share": {k: round(100.0 * v / (9 * L), 1) for k, v in game_slots.most_common()},
            "game_ge4_pct": {k: round(100.0 * v / L, 1) for k, v in game_ge4.most_common()},
            "stack_size_pct": {str(k): round(100.0 * v / L, 1) for k, v in sorted(stack.items())},
            "bring_back_pct": round(100.0 * bring / L, 1),
            "top_stacks": [{"stack": list(k), "pct": round(100.0 * v / L, 1)} for k, v in stack_keys.most_common(5)],
            "shared": pairs_shared(sets)}


def field_game_share(players):
    """The modelled field's (and the optimal lineups') share of roster slots
    by game: sum of a game's players' ownership / 9 slots. field_pct is the
    share of field lineups that hold the man, so the sum over players is 900."""
    out = {}
    for key in ("field_pct", "opt_pct", "casual_pct"):
        g = Counter()
        tot = 0.0
        for p in players:
            v = p.get(key)
            if v is None:
                continue
            g[p.get("game")] += v
            tot += v
        out[key] = {"total": round(tot, 1), "by_game": {k: round(100.0 * v / tot, 1) for k, v in g.most_common()}}
    return out


def sd_list_stats(rows, players, n=None):
    rows = rows[:n] if n else rows
    by = {p["name"]: p for p in players}
    L = len(rows)
    sets = [frozenset([r["captain"]] + list(r["flex"])) for r in rows]
    capsets = [(r["captain"], frozenset(r["flex"])) for r in rows]
    distinct = len(set(capsets))
    pc = Counter(nm for s in sets for nm in s)
    cpt = Counter(r["captain"] for r in rows)
    split = Counter()
    for r in rows:
        t = Counter(p["team"] for p in r["lineup"])
        split["-".join(str(v) for v in sorted(t.values(), reverse=True))] += 1
    # dominant team of each lineup (the 5-1 / 4-2 side)
    dom = Counter()
    for r in rows:
        t = Counter(p["team"] for p in r["lineup"])
        dom[t.most_common(1)[0][0]] += 1
    top_players = []
    for nm, k in pc.most_common(10):
        p = by.get(nm, {})
        top_players.append({"name": nm, "pos": p.get("pos"), "team": p.get("team"), "exp_pct": round(100.0 * k / L, 1),
                            "cpt_exp_pct": round(100.0 * cpt.get(nm, 0) / L, 1),
                            "field_cpt_pct": p.get("field_cpt_pct"), "field_flex_pct": p.get("field_flex_pct"),
                            "opt_cpt_pct": p.get("opt_cpt_pct"), "opt_flex_pct": p.get("opt_flex_pct")})
    same_cpt_pairs = sum(1 for a, b in itertools.combinations(rows, 2) if a["captain"] == b["captain"])
    npairs = L * (L - 1) // 2
    return {"n": L, "distinct": distinct, "players_used": len(pc),
            "captains": {k: round(100.0 * v / L, 1) for k, v in cpt.most_common(6)},
            "n_captains": len(cpt),
            "top_players": top_players,
            "team_split_pct": {k: round(100.0 * v / L, 1) for k, v in split.most_common()},
            "dominant_team_pct": {k: round(100.0 * v / L, 1) for k, v in dom.most_common()},
            "shared": pairs_shared(sets),
            "same_captain_pair_pct": round(100.0 * same_cpt_pairs / npairs, 1) if npairs else None}


def list_overlap(lists):
    """Lineup-level overlap between the lists (how many of list A's lineups
    are also in list B)."""
    keys = {k: {frozenset(r["names"]) if "names" in r else (r["captain"], frozenset(r["flex"])) for r in v}
            for k, v in lists.items()}
    out = {}
    ks = list(keys)
    for a, b in itertools.combinations(ks, 2):
        out[f"{a}&{b}"] = len(keys[a] & keys[b])
    allk = set().union(*keys.values())
    return {"pairwise_shared_lineups": out, "union_distinct": len(allk),
            "sizes": {k: len(v) for k, v in keys.items()}}


def marginal(p_any):
    prev = 0.0
    out = []
    for v in p_any:
        out.append(round(v - prev, 1))
        prev = v
    return out


def main():
    res = {}
    for dg in (156932, 157066):
        d = load(dg)
        pl = d["players"]
        b = {"kind": "classic", "label": None, "field_game_share": field_game_share(pl), "contests": {}}
        for c in d["contests"]:
            cid = str(c["id"])
            r = d["results"][cid]
            port = r["portfolio"]["entries"]
            lists = {"portfolio": port}
            for k in ("top_money", "top_top1", "top_top01", "top_win", "top_ev"):
                lists[k + "@20"] = r[k][:20]
            cs = {"contest": c["name"], "entries": c["max_entries"], "fee": c["entry_fee"],
                  "p_any_top1_pct": r["portfolio"]["p_any_top1_pct"],
                  "p_any_marginal": marginal(r["portfolio"]["p_any_top1_pct"]),
                  "port_entry_top1": [e["top1_pct"] for e in port],
                  "lists": {}}
            for k, v in lists.items():
                cs["lists"][k] = classic_list_stats(v, pl)
            for k in ("top_money", "top_top1", "top_win", "top_ev"):
                cs["lists"][k + "@8(shown)"] = classic_list_stats(r[k][:8], pl)
            cs["overlap"] = list_overlap(lists)
            # union of everything the board recommends in this contest
            allrows = port + sum((r[k][:40] for k in ("top_money", "top_top1", "top_top01", "top_win", "top_ev")), [])
            uniq = {}
            for rw in allrows:
                uniq.setdefault(frozenset(rw["names"]), rw)
            cs["union_all"] = classic_list_stats(list(uniq.values()), pl)
            b["contests"][cid] = cs
        res[str(dg)] = b
    for dg in (157074, 157075, 157072, 157086):
        d = load(dg)
        pl = d["players"]
        b = {"kind": "showdown", "contest": d["contest"]["name"], "entries": d["contest"]["max_entries"],
             "lists": {}, "portfolio": {}}
        for k in ("top_money", "top_top1", "top_win", "top_ev"):
            b["lists"][k + "@20"] = sd_list_stats(d[k][:20], pl)
        for k in ("5", "10", "20"):
            pf = d["portfolio"].get(k)
            if pf:
                b["portfolio"][k] = dict(sd_list_stats(pf["entries"], pl), p_any_top1_pct=pf["p_any_top1_pct"])
        lists = {"portfolio20": d["portfolio"]["20"]["entries"]}
        for k in ("top_money", "top_top1", "top_win", "top_ev"):
            lists[k + "@20"] = d[k][:20]
        b["overlap"] = list_overlap(lists)
        b["port20_entry_top1"] = [e["top1_pct"] for e in d["portfolio"]["20"]["entries"]]
        res[str(dg)] = b
    json.dump(res, open(os.path.join(HERE, "conc.json"), "w"), indent=1)
    print("ok", file=sys.stderr)


if __name__ == "__main__":
    main()
