"""A showdown board after its game: the served field against the real one,
and every lineup the board listed scored on the real points (2026-10-20).

DESCRIPTIVE. Nothing here changes a board, and nothing here is the S7
prospective test (research/s7_prospective.py scores the frozen field family
on the same contest). Inputs, all committed or archived:
  - the board as served (the snapshot workflow's errors/tourney-<dg>.json on
    sim-history; its pool rebuilds the served field exactly, checked against
    the board's own lineup count and beta);
  - the owner's final standings export (read here, never committed);
  - its committed summary (research/data/dk_fields/dk_field_<cid>.json).
Reported:
  1. the field: pair collision, distinct lineups, the most-copied build,
     salary, team split -- served against real -- and the share of real
     entries the served universe can even hold (every player in its pool);
  2. ownership: captain and flex MAE over the board's players, the served
     field's share against the board's best-lineup share (opt_*_pct);
  3. the one-knob check Sunday night suggested: FIELD_TOP_SHARE at the
     value whose collision matched Sunday's real field (research/sd_dup.py,
     0.468%) against the served 0.2%, on this game's geometry -- whether
     Sunday's knob carries to Monday. NOT a pre-registered test; the S7
     scorer is the one that judges fields;
  4. every listed lineup (top_win, top_ev, top_top1, top_money, the
     portfolios) on the real points: its rank in the real field (one plus
     the entries strictly above it), whether it cashed (rank within the paid
     places), and what it paid on DraftKings' own table with ties split the
     way DraftKings splits them; beside them the best lineup we could have
     entered on the real points (the hindsight ceiling, never a target).

python3 -m research.sd_postgame <board.json> <export.zip|csv> <summary.json> [out.json]
"""
import bisect
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNF_HOT_KNOB = 0.00468            # research/data/sd_dup.json snf.hot_knob
SERVED_KNOB = 0.002
LISTS = ("top_win", "top_ev", "top_top1", "top_money")


def _payout_table(detail):
    """[(from_rank, to_rank, prize)] off dk.contest_detail's payouts."""
    out = []
    for p in detail.get("payouts") or []:
        lo, hi, amt = p.get("from"), p.get("to"), p.get("prize")
        if lo and hi and amt is not None:
            out.append((int(lo), int(hi), float(amt)))
    return sorted(out)


def _prize_at(table, rank):
    for lo, hi, amt in table:
        if lo <= rank <= hi:
            return amt
    return 0.0


def _paid(table, rank, ties):
    """What one entry at `rank` with `ties` entries level with it (itself
    included) is paid: the tied places' prizes averaged (DraftKings)."""
    if not table:
        return None
    return sum(_prize_at(table, r) for r in range(rank, rank + ties)) / ties


def main(board_path, export_path, summary_path, out_path=None, log=print):
    sys.path.insert(0, ROOT)
    import numpy as np
    import dfs_tourney as T
    import dk
    import dk_standings as DS
    b = json.load(open(board_path))
    b = b.get("board", b)
    summ = json.load(open(summary_path))
    text, sha = DS.read_text(export_path)
    parsed = DS.parse(text)
    with_lu = [e for e in parsed["entries"] if e["lineup"]]
    N = len(with_lu)
    P = b["players"]
    idx, W, allowed = T.enumerate_showdown(P, 50000, cpt_mult=1.5)
    f, beta = T.field_weights(P, idx, cpt_mult=1.5)
    assert len(idx) == b["lineups_legal"] and round(beta, 4) == round(b["field_model"]["beta"], 4), (len(idx), beta)
    f_hot, beta_hot = T.field_weights(P, idx, cpt_mult=1.5, top_share=SNF_HOT_KNOB)
    sal = np.asarray([p["salary"] for p in P])
    csal = np.asarray([p["cpt_salary"] for p in P])
    lsal = csal[idx[:, 0]] + sal[idx[:, 1:]].sum(axis=1)
    order = np.argsort(lsal, kind="stable")

    def shape(ff):
        return {"pair_collision": float((ff ** 2).sum()),
                "distinct_expected": round(float((1.0 - (1.0 - ff) ** N).sum())),
                "top_copies": round(N * float(ff.max()), 1),
                "salary_mean": round(float((ff * lsal).sum())),
                "salary_median": int(lsal[order][np.searchsorted(np.cumsum(ff[order]), 0.5)])}
    pool = {DS._norm(p["name"]) for p in P}
    held = sum(1 for e in with_lu if all(DS._norm(nm) in pool for _s, nm in e["lineup"]))
    outside = {}
    for e in with_lu:
        for _s, nm in e["lineup"]:
            if DS._norm(nm) not in pool:
                outside[nm] = outside.get(nm, 0) + 1
    field = {"served": shape(f), "snf_knob": shape(f_hot), "snf_knob_beta": round(beta_hot, 4),
             "real": {"pair_collision": summ["duplicates"]["distinct_pair_collision"],
                      "distinct": summ["duplicates"]["distinct_lineups"], "top_copies": summ["duplicates"]["max_copies"],
                      "salary_mean": summ["shape"]["salary_mean"], "salary_median": summ["shape"]["salary_median"],
                      "team_split_pct": summ["shape"]["team_split_pct"]},
             "real_entries_the_universe_holds_pct": round(100.0 * held / N, 2),
             "players_outside_the_pool": dict(sorted(outside.items(), key=lambda kv: -kv[1])[:8])}
    # 2. ownership over the board's players
    cpt, anys = {}, {}
    for e in with_lu:
        for s, nm in e["lineup"]:
            k = DS._norm(nm)
            anys[k] = anys.get(k, 0) + 1
            if s == "CPT":
                cpt[k] = cpt.get(k, 0) + 1
    rc = [100.0 * cpt.get(DS._norm(p["name"]), 0) / N for p in P]
    rf = [100.0 * (anys.get(DS._norm(p["name"]), 0) - cpt.get(DS._norm(p["name"]), 0)) / N for p in P]

    def mae(x, y):
        return round(sum(abs(a - c) for a, c in zip(x, y)) / len(P), 3)
    own = {"cpt": {"field": mae([p["field_cpt_pct"] for p in P], rc), "opt": mae([p["opt_cpt_pct"] for p in P], rc)},
           "flex": {"field": mae([p["field_flex_pct"] for p in P], rf), "opt": mae([p["opt_flex_pct"] for p in P], rf)},
           "players": [{"name": p["name"], "field_cpt": p["field_cpt_pct"], "opt_cpt": p["opt_cpt_pct"], "real_cpt": round(rc[i], 2),
                        "field_flex": p["field_flex_pct"], "opt_flex": p["opt_flex_pct"], "real_flex": round(rf[i], 2)}
                       for i, p in enumerate(P)]}
    # 4. the listed lineups on the real points
    fp = {DS._norm(k): float(v or 0.0) for k, v in (parsed.get("fpts") or {}).items()}
    pts_vec = np.asarray([fp.get(DS._norm(p["name"]), 0.0) for p in P], dtype=np.float64)
    real_pts = sorted(e["points"] for e in parsed["entries"])
    detail = dk.contest_detail(int(summ["contest_id"])) or {}
    table = _payout_table(detail)
    places = int(detail.get("places_paid") or summ["contest"]["places_paid"])
    fee = float(detail.get("entry_fee") or summ["contest"]["entry_fee"])

    def place(score):
        above = len(real_pts) - bisect.bisect_right(real_pts, score + 1e-6)
        level = bisect.bisect_right(real_pts, score + 1e-6) - bisect.bisect_left(real_pts, score - 1e-6)
        rank = above + 1
        paid = _paid(table, rank, level + 1)          # we join the entries level with us
        return {"points": round(score, 2), "rank": rank, "cashed": rank <= places,
                "paid": None if paid is None else round(paid, 2)}

    def lineup_points(row):
        return 1.5 * fp.get(DS._norm(row["captain"]), 0.0) + sum(fp.get(DS._norm(n), 0.0) for n in row["flex"])
    lists = {}
    for lst in LISTS:
        rows = b.get(lst) or []
        got = [dict(place(lineup_points(r)), captain=r["captain"]) for r in rows]
        top = got[:20]
        lists[lst] = {"n": len(got), "top5": got[:5],
                      "top20_cashed": sum(1 for g in top if g["cashed"]),
                      "top20_best_rank": min((g["rank"] for g in top), default=None),
                      "top20_paid": round(sum(g["paid"] or 0.0 for g in top), 2),
                      "top20_fees": round(fee * len(top), 2)}
    ports = {}
    for k, v in (b.get("portfolio") or {}).items():
        got = [place(lineup_points(r)) for r in v.get("entries") or []]
        ports[k] = {"entries": len(got), "cashed": sum(1 for g in got if g["cashed"]),
                    "best_rank": min((g["rank"] for g in got), default=None),
                    "any_top1pct": any(g["rank"] <= max(1, len(real_pts) // 100) for g in got),
                    "paid": round(sum(g["paid"] or 0.0 for g in got), 2), "fees": round(fee * len(got), 2),
                    "board_p_any_top1_pct": v.get("p_any_top1_pct")}
    lp = W.astype(np.float64) @ pts_vec
    ok = np.where(np.asarray(allowed))[0]
    best_i = int(ok[np.argmax(lp[ok])])
    best = place(float(lp[best_i]))
    best["lineup"] = [P[idx[best_i, 0]]["name"] + " (CPT)"] + [P[idx[best_i, k]]["name"] for k in range(1, 6)]
    out = {"board": {"draft_group_id": b["draft_group_id"], "built_ts": b["built_ts"],
                     "mins_before_lock": b.get("mins_before_lock"), "sims": b.get("sims"),
                     "lineups_legal": b["lineups_legal"], "lineups_allowed": b["lineups_allowed"]},
           "export_sha256": sha, "entries_with_lineup": N, "places_paid": places, "entry_fee": fee,
           "cash_line": summ["points"].get("cash_line"), "first": summ["points"]["first"],
           "field": field, "ownership": own, "lists": lists, "portfolios": ports,
           "hindsight_best_allowed": best, "payout_table_rows": len(table)}
    if out_path:
        with open(out_path, "w") as fh:
            json.dump(out, fh, indent=1)
        log(f"[sdpg] wrote {out_path}")
    return out


if __name__ == "__main__":
    sys.path.insert(0, ROOT)
    os.environ.setdefault("VIGIL_NO_BG", "1")
    main(*sys.argv[1:5])
