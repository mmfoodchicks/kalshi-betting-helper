"""Question C (backlog 4.1): modelled ownership on the boards against real
ownership where a committed real-field summary exists.

Read-only. Real ownership: research/data/dk_fields (the 40 most-owned
players of each contest; `pct` any-slot, `cpt_pct` captain). Boards:
errors/tourney-<dg>.json from origin/sim-history, exported into data/.
Name matching goes through dk_standings._norm so it is the repo's own rule.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "/home/user/kalshi-betting-helper"
sys.path.insert(0, REPO)
import dk_standings as DS  # noqa: E402  (read-only import of the repo's name rule)


def mae(a, b):
    return round(sum(abs(x - y) for x, y in zip(a, b)) / max(1, len(a)), 2)


def bias(a, b):
    return round(sum(x - y for x, y in zip(a, b)) / max(1, len(a)), 2)


def load_board(dg):
    return json.load(open(os.path.join(HERE, "data", f"tourney-{dg}.json")))


def real_summary(cid):
    return json.load(open(os.path.join(REPO, "research", "data", "dk_fields", f"dk_field_{cid}.json")))


def classic(dg, cid):
    b = load_board(dg)
    s = real_summary(cid)
    by = {DS._norm(p["name"]): p for p in b["players"]}
    rows, missing = [], []
    for r in s["ownership"]:
        p = by.get(DS._norm(r["name"]))
        if p is None:
            missing.append(r["name"])
            continue
        rows.append((r["name"], r["pct"], p["field_pct"], p["opt_pct"], p.get("casual_pct")))
    real = [r[1] for r in rows]
    out = {"board": dg, "contest": cid, "engine": b.get("engine"), "built_ts": b["built_ts"],
           "field_model": b["field_model"].get("model") or "softmax (pre-ENGINE 10)",
           "n_matched": len(rows), "missing": missing,
           "top40_mae": {"field_pct": mae([r[2] for r in rows], real), "opt_pct": mae([r[3] for r in rows], real)},
           "top40_bias": {"field_pct": bias([r[2] for r in rows], real), "opt_pct": bias([r[3] for r in rows], real)},
           "shape_real": s["shape"], "shape_model": {k: b["field_model"].get(k) for k in ("mean_salary", "achieved")},
           "dup_real": s["duplicates"], "dup_model_collision": b["field_model"].get("collision"),
           "worst": sorted(({"name": r[0], "real": r[1], "field": r[2], "opt": r[3]} for r in rows),
                           key=lambda x: -abs(x["field"] - x["real"]))[:8]}
    if all(r[4] is not None for r in rows):
        out["top40_mae"]["casual_pct"] = mae([r[4] for r in rows], real)
    return out


def showdown(dg, cid):
    b = load_board(dg)
    s = real_summary(cid)
    by = {DS._norm(p["name"]): p for p in b["players"]}
    rows, missing = [], []
    for r in s["ownership"]:
        p = by.get(DS._norm(r["name"]))
        if p is None:
            missing.append(r["name"])
            continue
        flex_real = r["pct"] - r["cpt_pct"]
        rows.append((r["name"], r["cpt_pct"], flex_real, p["field_cpt_pct"], p["field_flex_pct"],
                     p["opt_cpt_pct"], p["opt_flex_pct"]))
    rc, rf = [r[1] for r in rows], [r[2] for r in rows]
    return {"board": dg, "contest": cid, "engine": b.get("engine"), "built_ts": b["built_ts"],
            "n_matched": len(rows), "missing": missing,
            "top40_mae": {"cpt": {"field": mae([r[3] for r in rows], rc), "opt": mae([r[5] for r in rows], rc)},
                          "flex": {"field": mae([r[4] for r in rows], rf), "opt": mae([r[6] for r in rows], rf)}},
            "top40_bias": {"cpt": {"field": bias([r[3] for r in rows], rc), "opt": bias([r[5] for r in rows], rc)},
                           "flex": {"field": bias([r[4] for r in rows], rf), "opt": bias([r[6] for r in rows], rf)}},
            "dup_real": s["duplicates"], "shape_real": s["shape"],
            "worst_cpt": sorted(({"name": r[0], "real": r[1], "field": r[3], "opt": r[5]} for r in rows),
                                key=lambda x: -abs(x["field"] - x["real"]))[:5]}


def modelled_only(dg):
    """The current boards: no real field yet. How far apart are the board's
    own three ownership columns?"""
    b = load_board(dg)
    P = b["players"]
    if b["kind"] == "classic":
        f = [p["field_pct"] for p in P]
        o = [p["opt_pct"] for p in P]
        c = [p.get("casual_pct") for p in P]
        tgt = [0.9 * oo + 0.1 * cc for oo, cc in zip(o, c)] if all(x is not None for x in c) else None
        top = sorted(P, key=lambda p: -p["field_pct"])[:40]
        return {"board": dg, "kind": "classic", "n": len(P), "field_model": b["field_model"].get("model"),
                "max_own_pct": b["field_model"].get("max_own_pct"),
                "mae_field_opt": mae(f, o), "mae_field_casual": mae(f, c) if tgt else None,
                "mae_field_target": mae(f, tgt) if tgt else None,
                "steer_final": (b["field_model"].get("steer") or {}).get("mae_to_targets_final"),
                "top40_by_field": {"mae_field_opt": mae([p["field_pct"] for p in top], [p["opt_pct"] for p in top]),
                                   "mae_field_casual": mae([p["field_pct"] for p in top], [p["casual_pct"] for p in top])},
                "biggest_casual_gap": sorted(({"name": p["name"], "field": p["field_pct"], "casual": p["casual_pct"],
                                               "opt": p["opt_pct"]} for p in P), key=lambda x: -abs(x["field"] - x["casual"]))[:6],
                "collision": b["field_model"].get("collision"),
                "top_share_pct": b["field_model"].get("top_share_pct"),
                "mean_salary": b["field_model"].get("mean_salary")}
    fc = [p["field_cpt_pct"] for p in P]
    oc = [p["opt_cpt_pct"] for p in P]
    ff = [p["field_flex_pct"] for p in P]
    of = [p["opt_flex_pct"] for p in P]
    return {"board": dg, "kind": "showdown", "n": len(P), "mae_cpt_field_opt": mae(fc, oc),
            "mae_flex_field_opt": mae(ff, of),
            "top_share_pct": (b.get("experimental") or {}).get("field_model", {}).get("top_share_pct"),
            "chalk_copies": (b["field_model"].get("chalk") or {}).get("expected_copies"),
            "biggest_cpt_gap": sorted(({"name": p["name"], "field_cpt": p["field_cpt_pct"], "opt_cpt": p["opt_cpt_pct"]}
                                       for p in P), key=lambda x: -abs(x["field_cpt"] - x["opt_cpt"]))[:5]}


def main():
    out = {"real": [classic(154941, 196653900), showdown(154959, 196786588), showdown(154960, 196786690)],
           "modelled": [modelled_only(dg) for dg in (156932, 157066, 157074, 157075, 157072, 157086)]}
    json.dump(out, open(os.path.join(HERE, "own.json"), "w"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
