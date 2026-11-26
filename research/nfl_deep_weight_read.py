"""The registered NFL deep-weight read, frozen (2026-11-26).

The rule, its ambiguities, how each is settled and the replacement protocol
are in research/reports/nfl_deep_weight_rule.md; this file is that rule as
code, and the report pins this file's sha256 (a guard holds the two
together, so an edit here without a logged reason in the report fails the
suite).

What it reads: the data branch's errors/nfl-deep-pairs-latest.json (the
error-log workflow pulls /api/predlog/paired?owner=nfl&shadow=nfl_deep,
nfl_book) and the history of errors/calib-params-latest.json there (the
calibrator in force when each drive row was logged).

What it prints BEFORE the trigger: counts only. No log loss, no difference,
no direction -- the interim record has been looked at once (the sprint's
engine audit, 57 games) and the rule is not to be steered by looking again.

    python research/nfl_deep_weight_read.py            # from origin/sim-history
    python research/nfl_deep_weight_read.py --pairs X.json --calib-history none
    python research/nfl_deep_weight_read.py --write    # after the trigger: the
                                                       # result to research/data/
"""
import argparse
import datetime as _dt
import json
import math
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..")
sys.path.insert(0, _ROOT)

WEIGHT = 0.30            # nfl_game_sim._DEEP_WEIGHT, frozen until this read (a guard holds them equal)
N_GAMES = 100            # "200 rows = 100 games" (the owner, 2026-11-26)
Z = 2.0                  # "beats the drive sim by > 2 SE", one-sided
CLIP = (0.001, 0.999)    # predlog.vs_market's clip, so the figures read as the record's
OWNER, SHADOW = "nfl", "nfl_deep"
PAIRS_PATH = "errors/nfl-deep-pairs-latest.json"
CALIB_PATH = "errors/calib-params-latest.json"

TO_ZERO = "WEIGHT_TO_ZERO"
EARNED = "STAYS_EARNED"
INCONCLUSIVE = "INCONCLUSIVE_STAYS_FROZEN"


def ll(p, y):
    p = min(CLIP[1], max(CLIP[0], float(p)))
    return -math.log(p) if y else -math.log(1.0 - p)


def scale(p, params):
    """calibrate.scale on the stored (t, q0, b, n); identity for None."""
    if not params:
        return p
    import calibrate
    t, q0, b = params[0], params[1], params[2]
    return calibrate.scale(p, t, q0, b)


def calib_history(ref="origin/sim-history", path=CALIB_PATH):
    """[(fit_ts, nfl params)] from every committed version, oldest first."""
    try:
        hs = subprocess.run(["git", "log", "--format=%H", ref, "--", path], cwd=_ROOT,
                            capture_output=True, text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return []
    out = {}
    for h in hs:
        try:
            d = json.loads(subprocess.run(["git", "show", f"{h}:{path}"], cwd=_ROOT, capture_output=True,
                                          text=True, check=True).stdout)
            got = (d.get("models") or {}).get(OWNER)
            if got and len(got) == 4 and d.get("ts"):
                out[float(d["ts"])] = tuple(got)
        except (subprocess.CalledProcessError, ValueError, TypeError):
            continue
    return sorted(out.items())


def params_at(hist, ts):
    """The newest fit made at or before `ts`; None (identity) before the first.
    A fit made after the row was logged is never used for it."""
    got = None
    for fit_ts, prm in hist:
        if fit_ts <= ts:
            got = prm
        else:
            break
    return got


def week_key(ts):
    """The NFL week as the Tuesday-to-Monday window (US Eastern) holding the
    kickoff, named by its Tuesday. No week numbering needed, and the
    Thanksgiving and international games fall in their own week."""
    try:
        from zoneinfo import ZoneInfo
        d = _dt.datetime.fromtimestamp(int(ts), ZoneInfo("America/New_York")).date()
    except Exception:
        d = (_dt.datetime.utcfromtimestamp(int(ts)) - _dt.timedelta(hours=5)).date()
    return (d - _dt.timedelta(days=(d.weekday() - 1) % 7)).isoformat()


def games(rows, hist):
    """Rows grouped into games (the event ticker: the market ticker less its
    side). Returns (graded, pending): graded games carry the per-game mean log
    loss of every model on the rows BOTH models logged; pending are games with
    a deep row not yet settled. Voided markets (graded 2) are neither."""
    by = {}
    for r in rows:
        if SHADOW not in (r.get("shadow") or {}) or r.get("prob") is None:
            continue
        ev = r["ticker"].rsplit("-", 1)[0]
        by.setdefault(ev, []).append(r)
    graded, pending = [], []
    for ev, rs in by.items():
        kick = min((r.get("event_ts") or r.get("close_time") or 0) for r in rs)
        if any(r.get("graded") == 0 for r in rs):
            pending.append({"event": ev, "kickoff": kick})
            continue
        ok = [r for r in rs if r.get("graded") == 1 and r.get("outcome") is not None]
        if not ok:
            continue
        acc = {k: 0.0 for k in ("drive", "deep", "blend", "drive_cal", "blend_cal")}
        kal, nk = 0.0, 0
        for r in ok:
            y = int(r["outcome"])
            pd, pp = float(r["prob"]), float(r["shadow"][SHADOW]["prob"])
            prm = params_at(hist, float(r.get("ts") or 0))
            pc = scale(pd, prm)
            acc["drive"] += ll(pd, y)
            acc["deep"] += ll(pp, y)
            acc["blend"] += ll((1 - WEIGHT) * pd + WEIGHT * pp, y)
            acc["drive_cal"] += ll(pc, y)
            acc["blend_cal"] += ll((1 - WEIGHT) * pc + WEIGHT * pp, y)
            if r.get("mkt") is not None:
                kal += ll(r["mkt"], y)
                nk += 1
        g = {k: v / len(ok) for k, v in acc.items()}
        g.update(event=ev, kickoff=kick, week=week_key(kick), rows=len(ok),
                 kalshi=(kal / nk if nk else None),
                 split_build=any(abs(float(r.get("ts") or 0) - float(r["shadow"][SHADOW].get("ts") or 0)) > 3600
                                 for r in ok))
        graded.append(g)
    graded.sort(key=lambda g: (g["kickoff"], g["event"]))
    pending.sort(key=lambda g: (g["kickoff"], g["event"]))
    return graded, pending


def dataset(graded, pending):
    """The frozen dataset: the first N_GAMES graded games by kickoff, read
    only once no game with a deep row that kicked off at or before the last of
    them is still unsettled. None until then."""
    if len(graded) < N_GAMES:
        return None
    ds = graded[:N_GAMES]
    last = ds[-1]["kickoff"]
    if any(p["kickoff"] <= last for p in pending):
        return None
    return ds


def clustered(ds, a, b):
    """Mean over games of (a - b) per game (positive: b scores better) and its
    standard error clustered by week: K/(K-1) x sum over weeks of the squared
    within-week residual sum, over G^2."""
    d = [g[a] - g[b] for g in ds]
    G = len(d)
    m = sum(d) / G
    wk = {}
    for g, x in zip(ds, d):
        wk[g["week"]] = wk.get(g["week"], 0.0) + (x - m)
    K = len(wk)
    se = math.sqrt(K / (K - 1) * sum(v * v for v in wk.values())) / G if K > 1 else float("nan")
    return {"mean": m, "se": se, "z": (m / se if se and se > 0 else float("nan")), "games": G, "weeks": K}


def decide(blend_vs_drive, deep_vs_drive):
    """The registered clauses, in order, plus the owner's resolution of the
    case they do not cover. By convexity of log loss the blend's gain is at
    least WEIGHT x the deep engine's, so the first two can never both hold."""
    if not blend_vs_drive["mean"] > 0:
        return TO_ZERO
    if deep_vs_drive["mean"] > Z * deep_vs_drive["se"]:
        return EARNED
    return INCONCLUSIVE


def read(rows, hist):
    graded, pending = games(rows, hist)
    ds = dataset(graded, pending)
    status = {"graded_games": len(graded), "graded_rows": sum(g["rows"] for g in graded),
              "pending_games": len(pending), "needed_games": N_GAMES, "triggered": ds is not None}
    if ds is None:
        return status
    reg = {"blend_vs_drive": clustered(ds, "drive", "blend"), "deep_vs_drive": clustered(ds, "drive", "deep")}
    prod = {"blend_vs_drive": clustered(ds, "drive_cal", "blend_cal"),
            "deep_vs_drive": clustered(ds, "drive_cal", "deep")}
    kal = [g for g in ds if g["kalshi"] is not None]
    status.update({
        "weight": WEIGHT,
        "dataset": [g["event"] for g in ds],
        "first_kickoff": ds[0]["kickoff"], "last_kickoff": ds[-1]["kickoff"],
        "weeks": sorted({g["week"] for g in ds}),
        "registered": {**reg, "decision": decide(reg["blend_vs_drive"], reg["deep_vs_drive"])},
        "production": {**prod, "decision_if_it_decided": decide(prod["blend_vs_drive"], prod["deep_vs_drive"])},
        "mean_logloss": {k: sum(g[k] for g in ds) / len(ds) for k in ("drive", "deep", "blend", "drive_cal",
                                                                       "blend_cal")},
        "kalshi_same_games": ({"games": len(kal), "kalshi": sum(g["kalshi"] for g in kal) / len(kal),
                               "drive": sum(g["drive"] for g in kal) / len(kal),
                               "blend_cal": sum(g["blend_cal"] for g in kal) / len(kal)} if kal else None),
        "split_build_games": sum(1 for g in ds if g["split_build"]),
    })
    status["decision"] = status["registered"]["decision"]
    status["production_agrees"] = status["production"]["decision_if_it_decided"] == status["decision"]
    return status


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pairs", default=None, help="a local export instead of origin/sim-history's")
    ap.add_argument("--calib-history", default="origin/sim-history",
                    help="git ref holding the calibration snapshots, or 'none' (identity throughout)")
    ap.add_argument("--write", action="store_true", help="after the trigger, write the result to research/data/")
    args = ap.parse_args()
    if args.pairs:
        with open(args.pairs) as f:
            exp = json.load(f)
    else:
        try:
            exp = json.loads(subprocess.run(["git", "show", f"origin/sim-history:{PAIRS_PATH}"], cwd=_ROOT,
                                            capture_output=True, text=True, check=True).stdout)
        except subprocess.CalledProcessError:
            print(f"[read] no {PAIRS_PATH} on origin/sim-history yet (git fetch origin sim-history; "
                  "the error-log workflow pulls it)")
            return 2
    hist = [] if args.calib_history == "none" else calib_history(args.calib_history)
    out = read(exp.get("rows") or [], hist)
    out["calibration_snapshots"] = len(hist)
    if not out["triggered"]:
        print(f"[read] not triggered: {out['graded_games']} of {N_GAMES} games graded "
              f"({out['graded_rows']} rows), {out['pending_games']} pending. No statistic is printed "
              "before the trigger (research/reports/nfl_deep_weight_rule.md).")
        return 0
    print(json.dumps(out, indent=1, default=str))
    if args.write:
        p = os.path.join(_HERE, "data", "nfl_deep_weight_read.json")
        with open(p, "w") as f:
            json.dump(out, f, indent=1, sort_keys=True, default=str)
        print(f"[read] written to {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
