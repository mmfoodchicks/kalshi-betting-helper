"""The 2027 family of coaching reads: how correlated their statistics are on shared
games, and what a multiplicity correction costs each in power
(research/reports/nfl_coach_family_2027.md). RESEARCH; reads only development
seasons (2025, 2026 weeks 1-12; R1's seal holds).

    python3 -m research.nfl_coach_family_2027 <pbp dir> <players.csv> <injuries dir>

Each registered read's per-unit statistic is computed on the SAME development
games with its frozen tables:
  R2-H2  per team-game: baseline minus candidate call log loss (nfl_coach_r2_adapt.score,
         adapt_r2.json's coefficients)
  R4     per pulled team-game: the joint decision gain (nfl_coach_r4_eval.decision)
  R5     per strategic pulled team-game: the second unit's log-likelihood over R4
         (nfl_coach_r5_eval.unit_score)
For two tests A, B over units SA, SB with per-unit statistics a_i, b_i, the
correlation of their MEANS is (n_shared / sqrt(nA nB)) x rho, rho the per-unit
correlation on the shared units: what the shared games can make of two passes.
Writes research/data/nfl_coach/family_2027.json.
"""
import json
import math
import os
import sys

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "family_2027.json")


def _phi(x):
    return 0.5 * math.erfc(-x / math.sqrt(2))


def z_of(alpha):
    lo, hi = 0.0, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if 1 - _phi(mid) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def holm(alpha, m):
    """One-sided z thresholds of Holm's steps at family-wise alpha over m tests."""
    return [round(z_of(alpha / (m - k)), 4) for k in range(m)]


def mean_corr(a, b):
    """Correlation of the two tests' means from per-unit dicts {unit: statistic}."""
    sh = sorted(set(a) & set(b))
    if len(sh) < 3:
        return {"shared": len(sh), "nA": len(a), "nB": len(b), "rho_shared": None, "corr_of_means": 0.0}
    xa = [a[k] for k in sh]
    xb = [b[k] for k in sh]
    ma, mb = sum(xa) / len(xa), sum(xb) / len(xb)
    sa = math.sqrt(sum((x - ma) ** 2 for x in xa) / (len(xa) - 1))
    sb = math.sqrt(sum((x - mb) ** 2 for x in xb) / (len(xb) - 1))
    rho = sum((x - ma) * (y - mb) for x, y in zip(xa, xb)) / (len(xa) - 1) / (sa * sb) if sa and sb else 0.0
    # rho's sampling SE (Fisher) beside it
    se = 1 / math.sqrt(max(1, len(sh) - 3))
    return {"shared": len(sh), "nA": len(a), "nB": len(b), "rho_shared": round(rho, 4), "rho_se": round(se, 3),
            "corr_of_means": round(len(sh) / math.sqrt(len(a) * len(b)) * rho, 4)}


def statistics(pbp, players_csv, inj, seasons=(2025, 2026)):
    from research import nfl_coach_r2_adapt as AD
    from research import nfl_coach_r2_data as R
    from research import nfl_coach_r3_qb as QB
    from research import nfl_coach_r4_data as XD
    from research import nfl_coach_r4_eval as EV4
    from research import nfl_coach_r4_fit as F4
    from research import nfl_coach_r5_eval as EV5
    from research import nfl_coach_r5_fit as F5
    import nfl_coach_sim_r4 as E4
    import nfl_coach_sim_r5 as E5
    ad = json.load(open(AD.OUT))
    allg, pos, ids_of = QB.load_all(pbp, players_csv, tuple(range(2016, 2027)))
    cls = F4.make_cls(F4.reports(inj, range(2016, 2026)))
    X, P = E4.tables(), E5.tables()
    out = {}
    for s in seasons:
        extra = {s: XD.load_extra(pbp, s)}
        h2 = {}
        rw = AD.rows(R.load_season(pbp, s))
        for (k, _g), (l0, l1, _n) in zip(rw, AD.score(rw, ad["coef_baseline"], ad["coef_candidate"])):
            h2[k] = l0 - l1
        pulled = EV4.collect_with_calls({s: allg[s]}, ids_of, extra, pos, cls)
        rows4 = EV4.decision(pulled, X)
        EV4.summarize(rows4)
        r4 = {(g["tg"]["season"], g["tg"]["week"], g["tg"]["team"]): r["joint"] for g, r in zip(pulled, rows4)}
        r5 = {}
        for tg in allg[s]:
            if cls(tg, ids_of[s], extra[s]) != "strategic":
                continue
            one = F5.unit_rows({s: allg[s]}, ids_of, pos,
                               lambda t, ids, e, me=tg: cls(t, ids, e) if t is me else None, extra, (s,))
            if one:
                r5[(s, tg["week"], tg["team"])] = EV5.unit_score(one, P, X)[0][0]
        out[s] = {"R2-H2": h2, "R4": r4, "R5": r5}
    return out


def main(pbp, players_csv, inj):
    st = statistics(pbp, players_csv, inj)
    res = {"seasons": [2025, "2026 weeks 1-12"], "pairs": {}, "n": {}}
    for s, d in st.items():
        res["n"][str(s)] = {k: len(v) for k, v in d.items()}
        for a, b in (("R2-H2", "R4"), ("R2-H2", "R5"), ("R4", "R5")):
            res["pairs"].setdefault(f"{a} x {b}", {})[str(s)] = mean_corr(d[a], d[b])
    # the family and its correction: the three 2027 primaries, each registered at z > 2
    # (one-sided 0.0228); Holm at a family-wise one-sided 0.05 over the three
    th = holm(0.05, 3)
    eff = {"R2-H2": {"ratio": 0.042 / 0.013, "note": "dev mean 0.042 at SE ~0.013 (640 team-games)"},
           "R2-H2 (last two seasons)": {"ratio": 0.025 / 0.013, "note": "0.025, the 2025-26 level"},
           "R4": {"ratio": 3.2, "note": "the low end of R4's registered z ~3.2-5.0 (~125 pulled team-games)"},
           "R5 (2027, ~45)": {"ratio": 1.34 / (2.68 / math.sqrt(45)), "note": "+1.34 at SD 2.68, 45 strategic pulls"},
           "R5 (2027 floor, 30)": {"ratio": 1.34 / (2.68 / math.sqrt(30)), "note": "the 30-game floor"}}
    pw = {}
    for k, v in eff.items():
        r = v["ratio"]
        pw[k] = {"effect_over_se": round(r, 3), "note": v["note"], "own_rule_z2": round(_phi(r - 2.0), 4),
                 "holm_first_step": round(_phi(r - th[0]), 4), "holm_last_step": round(_phi(r - th[-1]), 4)}
    indep = 1 - (1 - (1 - _phi(2.0))) ** 3
    res["family"] = {"tests": ["R2-H2 criterion 2", "R4 criterion 2", "R5 criterion 2 (2027 amendment)"],
                     "each_registered_one_sided": round(1 - _phi(2.0), 5),
                     "uncorrected_familywise_if_independent": round(indep, 4),
                     "holm_familywise_one_sided": 0.05, "holm_z_thresholds": th, "power": pw}
    with open(OUT, "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
    return res


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], sys.argv[2], sys.argv[3]), indent=1))
