"""The registered read of the showdown house rules (research/data/dfs_house_rules/prereg.json; written and
committed before any recorded slate was scored).

    python3 research/dfs_house_rules_read.py          # prints the verdict; --write saves read.json

Six primaries, each a house rule lifted alone against conservative (the captain-salary floor, kicker/defense
captains, the opposing defense beside a captain, two kickers/defenses, one tight end a team, the cheap-punt
rules), Holm-corrected across the six at a family-wise one-sided 5%; the joint relaxation (every rule lifted) is a
secondary that decides nothing. The statistic is the OPTIMIZER'S CHOSEN entries -- the 20-entry portfolio each
variant's own ranking picks on the choosing worlds -- scored on the independent worlds (P(any in the top 1%)),
one number per slate, the SE over SLATES. How often each rule removes the hindsight-optimal lineup (optimal-lineup
eligibility) is reported beside it and never decides. Real DraftKings points of the chosen entries, once the games
are final, are reported (SE over slates) and never decide. Reads the same prospective recordings as the
eligibility read (research/dfs_eligibility_read.py); nothing here is tunable.
"""
import glob
import json
import math
import os
import sys

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(_ROOT, "research", "data", "dfs_house_rules")
PREREG = os.path.join(DATA, "prereg.json")
PROSPECTIVE = os.path.join(_ROOT, "research", "data", "dfs_eligibility", "prospective")


def _betacf(a, b, x):
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / (c if abs(c) > 1e-300 else 1e-300)
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / (c if abs(c) > 1e-300 else 1e-300)
        dl = d * c
        h *= dl
        if abs(dl - 1.0) < 1e-12:
            break
    return h


def _ibeta(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1 - x) / b


def t_sf(t, df):
    """P(T > t) for Student's t with df degrees of freedom."""
    x = df / (df + t * t)
    tail = 0.5 * _ibeta(df / 2.0, 0.5, x)
    return tail if t > 0 else 1.0 - tail


def holm(pvals, alpha):
    """{name: reject?} by Holm's step-down at family-wise `alpha`."""
    order = sorted(pvals, key=lambda k: pvals[k])
    out, m, stop = {}, len(order), False
    for i, k in enumerate(order):
        if not stop and pvals[k] <= alpha / (m - i):
            out[k] = True
        else:
            stop = True
            out[k] = False
    return out


def _mean_se(xs):
    n = len(xs)
    m = sum(xs) / n
    if n < 2:
        return m, float("inf")
    return m, math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) / math.sqrt(n)


def real_points_from_box(c, variant, now_ts=None):
    """The mean real DraftKings points of a variant's 20 chosen entries minus conservative's, once the game is
    final (lock + 48 h; Sleeper's box via research/dfs_eligibility_history.py), else None. Captain at 1.5x."""
    import time
    import dfs_eligibility_history as H
    meta = c.get("meta") or {}
    if (now_ts or time.time()) < (meta.get("lock_ts") or 0) + 48 * 3600:
        return None
    acts = H.actuals(int(c["slate"]["week"]))
    who = {p["name"]: p for p in c.get("players") or []}

    def pts(lineups):
        tot = []
        for lu in lineups or []:
            vals = [H.real(acts, nm, who[nm]["pos"], who[nm]["team"]) or 0.0 for nm in lu]
            tot.append(1.5 * vals[0] + sum(vals[1:]))
        return sum(tot) / len(tot) if tot else None
    a = pts(c["summary"][variant].get("portfolio_20"))
    b = pts(c["summary"]["conservative"].get("portfolio_20"))
    return None if a is None or b is None else a - b


def read(files=None, reg=None, now_ts=None, real_points=None):
    import dfs_eligibility_read as ER
    reg = reg or json.load(open(PREREG))
    files = sorted(glob.glob(os.path.join(PROSPECTIVE, "compare_*.json"))) if files is None else files
    slates = []
    for f in files:
        c = json.load(open(f)) if isinstance(f, str) else f
        if ER.eligible(c, reg) and all(v in c["summary"] for v in reg["primaries"].values()):
            slates.append(c)
    slates.sort(key=lambda c: c["meta"]["lock_ts"])
    slates = slates[:reg["n_target"]]
    n = len(slates)
    out = {"n": n, "dg": [c["meta"]["dg"] for c in slates]}
    late = (now_ts or 0) >= reg["last_read_ts"]
    if n < reg["n_target"] and not late:
        out["verdict"] = "NOT READY"
        return out
    if n < reg["n_min"]:
        out["verdict"] = "INCONCLUSIVE"
        return out
    res, pv = {}, {}
    for name, v in list(reg["primaries"].items()) + [("joint", reg["secondary"])]:
        d = [c["summary"][v]["p20_vs_conservative"]["top1"][0] for c in slates]
        cash = [c["summary"][v]["p20_vs_conservative"]["cash"][0] for c in slates]
        cover = [c["coverage"][v]["share"] - c["coverage"]["conservative"]["share"] for c in slates]
        D, se = _mean_se(d)
        C, cse = _mean_se(cash)
        t = D / se if se > 0 else (float("inf") if D > 0 else 0.0)
        p = t_sf(t, n - 1) if math.isfinite(t) else (0.0 if t > 0 else 1.0)
        res[name] = {"variant": v, "D": D, "se": se, "t": t, "p_one_sided": p, "cash": C, "cash_se": cse,
                     "cash_ok": C + 2 * cse >= 0, "coverage_gain_mean": sum(cover) / n}
        if name != "joint":
            pv[name] = p
    rej = holm(pv, reg["alpha_familywise_one_sided"])
    for name in pv:
        res[name]["holm_reject"] = rej[name]
        res[name]["verdict"] = ("PASS: lifting it MAY become the default, with the owner's sign-off"
                                if rej[name] and res[name]["D"] > 0 and res[name]["cash_ok"] else "FAIL: the rule stays")
    res["joint"]["verdict"] = "secondary: reported, decides nothing"
    if real_points is not None:
        # real results where the games are final: the chosen entries' mean real DraftKings points, lifted minus
        # conservative, SE over slates -- reported, never decisive (one world a slate)
        for name, r in res.items():
            diffs = []
            for c in slates:
                got = real_points(c, r["variant"])
                if got is not None:
                    diffs.append(got)
            if len(diffs) >= 2:
                m, se = _mean_se(diffs)
                r["real_points_diff"] = {"slates": len(diffs), "mean": m, "se": se}
    out["rules"] = res
    out["verdict"] = {k: res[k]["verdict"] for k in pv}
    return out


if __name__ == "__main__":
    import time
    got = read(now_ts=time.time(), real_points=real_points_from_box)
    print(json.dumps(got, indent=1, default=str))
    if "--write" in sys.argv:
        with open(os.path.join(DATA, "read.json"), "w") as fh:
            json.dump(got, fh, indent=1, sort_keys=True, default=str)
