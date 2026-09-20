"""Task 8, ranking diagnostic: how far do eleven enumerated simple per-player
statistics of projection and salary go toward the real chalk order? FIT-FREE.
(A finite set, not the space of all monotone functions: the result bounds
these statistics, nothing broader.)

The third artifact (research/cl_mech.py) shows the current family's whole
kappa grid leaves the real top-50 ordering where the served build had it.
Before a new family is proposed, this bounds the problem from the other
side: for a small enumerated set of per-player statistics on the served
pre-lock pool (Sleeper's line as production uses it, value per $1,000, the
additive price-adjusted line the sampler ranks by at each grid kappa, and
DraftKings' own season average from the pinned slate as a second public
projection source), the Spearman correlation with the real any-slot
ownership over the 240 pool players and over the real top 50, the overlap
of each statistic's top 20 with the real top 20, and the same within each
position (ownership is decided slot by slot). Nothing is chosen to fit: the
set is enumerated and every member is reported.

    python3 -m research.cl_rank
"""
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research import cl_field as C  # noqa: E402
from research import cl_sampler as S  # noqa: E402

DATA = C.DATA
KAPPAS = (-2.0, -1.0, 0.0, 0.5, 1.0, 2.0)
POSITIONS = ("QB", "RB", "WR", "TE", "DST")


def statistics(ents, dk_avg):
    """{name: (P,) array} of the enumerated per-player statistics."""
    proj = np.asarray([e["proj"] for e in ents], dtype=np.float64)
    sal = np.asarray([e["salary"] for e in ents], dtype=np.float64) / 1000.0
    avg = np.asarray([dk_avg.get(e["name"], np.nan) for e in ents], dtype=np.float64)
    out = {"sleeper_proj": proj, "sleeper_proj_per_1k": proj / sal, "dk_avg_points": avg, "dk_avg_per_1k": avg / sal, "salary": sal}
    for k in KAPPAS:
        out[f"sleeper_proj_minus_{k:g}_per_1k"] = proj - k * sal
    return out


def _spearman(x, y):
    m = ~(np.isnan(x) | np.isnan(y))
    if m.sum() < 3:
        return None
    return round(float(np.corrcoef(S._rankdata(x[m]), S._rankdata(y[m]))[0, 1]), 4)


def grade(stat, real, pos, top=50):
    order_r = np.argsort(-real)
    t50, t20 = order_r[:top], order_r[:20]
    valid = ~np.isnan(stat)
    top20_stat = set(np.argsort(-np.where(valid, stat, -np.inf))[:20].tolist())
    out = {"spearman_all": _spearman(stat, real), "spearman_real_top50": _spearman(stat[t50], real[t50]),
           "top20_overlap_with_real_top20": int(len(top20_stat & set(t20.tolist()))),
           "by_position": {}}
    for p in POSITIONS:
        m = np.asarray([q == p for q in pos])
        out["by_position"][p] = {"players": int(m.sum()), "spearman": _spearman(stat[m], real[m])}
    return out


def run(log=print):
    from research import provenance, s6_capture
    import simulate
    d, man = S.served_board()
    ents = S.served_pool(d)
    real_art = S.real_side()
    real_own = real_art["empirical"]["ownership_any_slot_pct"]
    slate, _details = s6_capture.replay(S.DG, capdir=S.CAPDIR)
    dk_avg = {}
    for r in simulate.parse_dk_csv(slate["csv"]):
        try:
            dk_avg[C._norm(r["name"])] = float(r.get("proj") or 0.0)
        except (TypeError, ValueError):
            pass
    real = np.asarray([float(real_own.get(e["name"], 0.0)) for e in ents])
    pos = [e["pos"] for e in ents]
    stats = statistics(ents, dk_avg)
    table = {name: grade(v, real, pos) for name, v in stats.items()}
    # the real top 20, with each statistic's rank of the player in the pool
    order_r = np.argsort(-real)[:20]
    ranks = {name: S._rankdata(-np.where(np.isnan(v), -np.inf, v)) for name, v in stats.items()}
    top20 = [{"name": ents[i]["name"], "pos": ents[i]["pos"], "salary": ents[i]["salary"], "sleeper_proj": ents[i]["proj"],
              "dk_avg_points": dk_avg.get(ents[i]["name"]), "real_pct": round(float(real[i]), 2),
              "rank_by": {name: int(ranks[name][i]) for name in ("sleeper_proj", "sleeper_proj_per_1k", "dk_avg_points", "dk_avg_per_1k")}} for i in order_r]
    out = {"meta": {"stage": "Task 8, ranking diagnostic: Spearman of enumerated per-player statistics with the real any-slot ownership on the served pre-lock pool, fit-free",
                    "statistics": sorted(stats), "kappas": list(KAPPAS), "pool": len(ents), "dk_avg_matched": int(sum(1 for e in ents if e["name"] in dk_avg)),
                    "inputs": {"served_board": man["sha256"], "first_artifact": "research/data/cl_field.json", "draftkings": s6_capture.dk_hashes(S.DG, capdir=S.CAPDIR)},
                    "fit_free": True, "note": "every member of the enumerated set is reported; none is chosen",
                    "provenance": provenance.stamp(worlds=None, model="none: rank correlations of fixed per-player statistics with the real field", seed="UNSEEDED")},
           "table": table, "real_top20": top20}
    path = os.path.join(DATA, "cl_rank.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    for name, t in table.items():
        log(f"[RANK] {name:34} all {t['spearman_all']} | real top50 {t['spearman_real_top50']} | top20 overlap {t['top20_overlap_with_real_top20']} | "
            + " ".join(f"{p} {t['by_position'][p]['spearman']}" for p in POSITIONS))
    return out


if __name__ == "__main__":
    run()
