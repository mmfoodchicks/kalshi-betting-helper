"""Fumble-return touchdowns: the probability the deep engine is missing, MEASURED (2026-11-27/28).

nfl_deep_sim plays pick-sixes and kick/punt return touchdowns but no fumble-return touchdown: a lost fumble
from scrimmage hands the ball over at a spot drawn from `fum_spot`, a table research/nfl_deep_fit.py builds
from the NON-touchdown lost fumbles only (`not (fl & P.F_TD)`). The touchdowns were dropped from the spot
table while still counted in the lost-fumble rate, so nothing in the engine stands for them. Real regular
seasons (the orchestrator's count, scratchpad rates.json): 0.040 fumble-return TDs from scrimmage per
team-game 2016-2025, 8.6% of lost fumbles.

The engine's lost fumbles happen at three places in nfl_deep_sim._snap: a sack, a completion, a run. Each
knows the snap's yards to go (`ytg`, nflverse's `yardline_100`), so the model may use the kind and the field
position and nothing else.

Input: a directory of nflverse-data play_by_play_<season>.csv.gz (CC BY 4.0; never committed). The output
(research/data/fumble_td_fit.json) is counts and fitted coefficients only.

THE RULE (written before any held-out season was read; `heldout` refuses unless this file is committed
unmodified, and records its sha256 and commit):

Rows. Regular-season plays with fumble_lost == 1 that are not special-teams plays (special_teams_play, or a
punt / kickoff / field goal / extra point). The outcome is a defensive return touchdown: return_touchdown == 1
and td_team == defteam. Kind: sack == 1 -> sack; complete_pass == 1 -> comp; anything else -> rush (the
engine's third site; ten fit-season rows are pass plays that are neither, 0 TDs). Field position: the snap's
yardline_100 (the offense's yards to the opponent's goal, 1-99). 2026 week 13 on is never read (the coaching
seal, research/nfl_coach_data.py).

Candidates (forms x windows):
- forms: M0 a constant; M1 the kind (3 levels); M2 the kind + a straight logit line in yardline_100; M3 the
  kind + five 20-yard zones of yardline_100 (1-20, 21-40, 41-60, 61-80, 81-99), additive in the logit.
- windows: ALL (every fit season from 2016 before the target); W5 (the five seasons before it).
The fit seasons are 2016-2024. The descriptive look that suggested the kind and the zones (sack 13.6% /
comp 5.5% / rush 6.4%; 81-99 24.3% against 1-20 3.3%) read 2016-2024 only.

Selection on the fit seasons only, by forward chaining: each target season 2019-2024 is predicted by every
candidate fitted on its window's earlier seasons; the score is the mean log loss per fumble over all six
targets. The ONE-SE RULE picks the simplest candidate whose mean loss is within one standard error (of the
per-fumble paired difference) of the best candidate's. Simplicity: parameters first (M0 1, M1 3, M2 4,
M3 7), then ALL before W5 (more data). The selected candidate is refitted on its window ending 2024
(ALL: 2016-2024; W5: 2020-2024) and those coefficients ship.

The held-out check (2025 and 2026 weeks 1-12, read ONCE after this rule is committed), shipped coefficients
fixed (no refit on held-out data):
- (a) the shipped model's held-out log loss may not be worse than M0 on the same window by more than one
  paired SE; if it is, M0 on that window ships instead.
- (b) the held-out touchdown count must lie inside the 95% interval of the shipped model's predicted count
  (sum p +/- 1.96 sqrt(sum p(1-p))). A miss is REPORTED as "level not confirmed", nothing is refitted, and
  the flip waits for the owner.
The held-out seasons' AGGREGATE rate was seen before this rule (the orchestrator's rates.json: 2025 17 of
220, 2026 wk 1-12 7 of 155); their split by kind and field position was not.

usage: python3 research/fumble_td_fit.py fit <nflverse dir>       # selection, fit seasons only
       python3 research/fumble_td_fit.py heldout <nflverse dir>   # the one held-out read (committed file)
"""
import csv
import gzip
import hashlib
import json
import math
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "research", "data", "fumble_td_fit.json")
FIT_SEASONS = tuple(range(2016, 2025))
TARGETS = tuple(range(2019, 2025))
HELDOUT = ((2025, 99), (2026, 13))            # (season, first week NOT read)
SEAL_SEASON, SEAL_WEEK = 2026, 13
KINDS = ("sack", "comp", "rush")
FORMS = {"M0": 1, "M1": 3, "M2": 4, "M3": 7}
WINDOWS = ("ALL", "W5")
ZONE_EDGES = (20, 40, 60, 80)                 # yardline_100 1-20 | 21-40 | 41-60 | 61-80 | 81-99
_ST = ("punt", "kickoff", "field_goal", "extra_point")


def zone(y):
    return sum(1 for e in ZONE_EDGES if y > e)


def kind_of(r):
    if r["sack"] == "1":
        return "sack"
    if r["complete_pass"] == "1":
        return "comp"
    return "rush"


def rows(d, season, stop_week=99):
    """Every lost fumble from scrimmage of one regular season: (kind, yardline_100, td)."""
    if season > SEAL_SEASON or (season == SEAL_SEASON and stop_week > SEAL_WEEK):
        raise SystemExit("the coaching seal: 2026 week 13 on is never read")
    out = []
    with gzip.open(os.path.join(d, f"play_by_play_{season}.csv.gz"), "rt", newline="") as fh:
        for r in csv.DictReader(fh):
            if r["season_type"] != "REG" or int(r["week"] or 0) >= stop_week or r["fumble_lost"] != "1":
                continue
            if r["special_teams_play"] == "1" or r["play_type"] in _ST:
                continue
            y = max(1, min(99, int(float(r["yardline_100"] or 50))))
            td = int(r["return_touchdown"] == "1" and r["td_team"] == r["defteam"])
            out.append((kind_of(r), y, td))
    return out


def design(form, k, y):
    """The feature vector of one fumble under `form` (an intercept first)."""
    x = [1.0]
    if form == "M0":
        return x
    x += [1.0 if k == "comp" else 0.0, 1.0 if k == "rush" else 0.0]      # sack is the base kind
    if form == "M2":
        x.append((y - 50) / 50.0)
    elif form == "M3":
        z = zone(y)
        x += [1.0 if z == j else 0.0 for j in (0, 1, 3, 4)]               # zone 2 (41-60) is the base
    return x


def _sig(z):
    return 1.0 / (1.0 + math.exp(-z)) if z >= 0 else math.exp(z) / (1.0 + math.exp(z))


def fit(form, data, ridge=1e-6, iters=50):
    """Logistic regression by Newton's method (a tiny ridge for a cell with no touchdown)."""
    X = [design(form, k, y) for k, y, _ in data]
    Y = [t for _, _, t in data]
    p = len(X[0])
    b = [0.0] * p
    for _ in range(iters):
        g = [-ridge * bj for bj in b]
        H = [[(ridge if i == j else 0.0) for j in range(p)] for i in range(p)]
        for x, yv in zip(X, Y):
            mu = _sig(sum(bi * xi for bi, xi in zip(b, x)))
            w = mu * (1 - mu)
            for i in range(p):
                g[i] += (yv - mu) * x[i]
                for j in range(p):
                    H[i][j] += w * x[i] * x[j]
        step = _solve(H, g)
        b = [bi + si for bi, si in zip(b, step)]
        if max(abs(s) for s in step) < 1e-10:
            break
    return b


def _solve(A, v):
    n = len(v)
    M = [row[:] + [v[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[piv] = M[piv], M[c]
        for r in range(n):
            if r != c and M[c][c]:
                f = M[r][c] / M[c][c]
                M[r] = [a - f * bb for a, bb in zip(M[r], M[c])]
    return [M[i][n] / M[i][i] for i in range(n)]


def predict(form, b, k, y):
    return _sig(sum(bi * xi for bi, xi in zip(b, design(form, k, y))))


def logloss(p, t):
    p = min(1 - 1e-9, max(1e-9, p))
    return -math.log(p if t else 1 - p)


def window(w, target):
    lo = 2016 if w == "ALL" else max(2016, target - 5)
    return tuple(range(lo, target))


def _mean_se(d):
    n = len(d)
    m = sum(d) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1)) if n > 1 else 0.0
    return m, sd / math.sqrt(n)


def select(by_season):
    """The forward chain and the one-SE rule; returns the table and the chosen (form, window)."""
    losses = {}
    for form in FORMS:
        for w in WINDOWS:
            ll = []
            for s in TARGETS:
                train = [r for ss in window(w, s) for r in by_season[ss]]
                b = fit(form, train)
                ll += [logloss(predict(form, b, k, y), t) for k, y, t in by_season[s]]
            losses[(form, w)] = ll
    mean = {c: sum(v) / len(v) for c, v in losses.items()}
    best = min(mean, key=mean.get)
    table = {}
    for c, v in losses.items():
        m, se = _mean_se([a - bb for a, bb in zip(v, losses[best])])
        table[f"{c[0]}/{c[1]}"] = {"mean_logloss": round(mean[c], 6), "minus_best": round(m, 6),
                                   "se_vs_best": round(se, 6), "params": FORMS[c[0]]}
    order = sorted(losses, key=lambda c: (FORMS[c[0]], WINDOWS.index(c[1])))
    chosen = next(c for c in order if table[f"{c[0]}/{c[1]}"]["minus_best"] <= table[f"{c[0]}/{c[1]}"]["se_vs_best"])
    return table, best, chosen, sum(len(by_season[s]) for s in TARGETS)


def describe(data):
    out = {}
    for k in KINDS:
        sub = [r for r in data if r[0] == k]
        out[k] = {"n": len(sub), "td": sum(r[2] for r in sub)}
    for z in range(5):
        sub = [r for r in data if zone(r[1]) == z]
        out[f"zone{z}"] = {"n": len(sub), "td": sum(r[2] for r in sub)}
    out["all"] = {"n": len(data), "td": sum(r[2] for r in data)}
    return out


def _rule_sha():
    with open(os.path.abspath(__file__), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def main_fit(d):
    by_season = {s: rows(d, s) for s in FIT_SEASONS}
    table, best, chosen, n_targets = select(by_season)
    lo = window(chosen[1], 2025)
    train = [r for s in lo for r in by_season[s]]
    b = fit(chosen[0], train)
    b0 = fit("M0", train)
    res = {"rule_sha256": _rule_sha(), "fit_seasons": list(FIT_SEASONS), "targets": list(TARGETS),
           "forward_chain_fumbles": n_targets, "candidates": table, "best": f"{best[0]}/{best[1]}",
           "chosen": {"form": chosen[0], "window": chosen[1], "seasons": [lo[0], lo[-1]], "coef": b,
                      "coef_m0": b0},
           "describe_fit": {str(s): describe(by_season[s]) for s in FIT_SEASONS},
           "describe_fit_all": describe([r for s in FIT_SEASONS for r in by_season[s]])}
    with open(OUT, "w") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps({k: res[k] for k in ("candidates", "best", "chosen")}, indent=1))


def main_heldout(d):
    rel = os.path.relpath(os.path.abspath(__file__), ROOT)
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=ROOT, capture_output=True).returncode == 0
    clean = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", rel], cwd=ROOT).returncode == 0
    if not (tracked and clean):
        raise SystemExit("the held-out read runs only from the committed rule (this file tracked and unmodified)")
    res = json.load(open(OUT))
    if res.get("heldout"):
        raise SystemExit("the held-out seasons were already read once")
    if res["rule_sha256"] != _rule_sha():
        raise SystemExit("the fit was made under another version of the rule")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    ch = res["chosen"]
    data = [r for s, w in HELDOUT for r in rows(d, s, w)]
    ll = [logloss(predict(ch["form"], ch["coef"], k, y), t) for k, y, t in data]
    l0 = [logloss(predict("M0", ch["coef_m0"], k, y), t) for k, y, t in data]
    m, se = _mean_se([a - b for a, b in zip(ll, l0)])
    ps = [predict(ch["form"], ch["coef"], k, y) for k, y, _ in data]
    exp_n, sd_n = sum(ps), math.sqrt(sum(p * (1 - p) for p in ps))
    obs = sum(t for _, _, t in data)
    a_ok = m <= se
    b_ok = abs(obs - exp_n) <= 1.96 * sd_n
    res["heldout"] = {"commit": head, "seasons": "2025 + 2026 weeks 1-12", "n": len(data), "td": obs,
                      "expected_td": round(exp_n, 2), "sd_td": round(sd_n, 2), "z": round((obs - exp_n) / sd_n, 3),
                      "logloss_shipped": round(sum(ll) / len(ll), 6), "logloss_m0": round(sum(l0) / len(l0), 6),
                      "diff_vs_m0": round(m, 6), "se": round(se, 6), "a_pass": a_ok, "b_pass": b_ok,
                      "describe": {f"{s}": describe(rows(d, s, w)) for s, w in HELDOUT},
                      "ships": ch["form"] if a_ok else "M0",
                      "level": "confirmed" if b_ok else "level not confirmed"}
    with open(OUT, "w") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps(res["heldout"], indent=1))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "fit":
        main_fit(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "heldout":
        main_heldout(sys.argv[2])
    else:
        print(__doc__)
