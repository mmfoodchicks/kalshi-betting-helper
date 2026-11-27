"""The CONFIRMATORY test of R1's criterion 4 (late-state win probability), on
fresh games only: the 2027 regular season. Registered in
research/data/nfl_coach/prereg_c4.json BEFORE R1's own read, and used ONLY if
that read fails criterion 4 and nothing else. R1's registered verdict is never
changed by it (research/reports/nfl_coach_followup.md section 2).

  python3 -m research.nfl_coach_c4_confirm <dir with play_by_play_2027.csv.gz> --look 1|2

The same frozen engine, tables, anchor, checkpoints (5:00 and 2:00 of the
fourth quarter), clip and paired per-game statistic as R1's criterion 4, by
R1's own pinned code. What is new is the error control:

  * the family is R1's criterion 4 and this test; its one-sided false-positive
    rate is 5%: R1's "more than 2 SE" spends 2.43% (Student t, 89 df, the
    fewest games its read accepts) and this test the other 2.57% (Bonferroni);
  * two looks, O'Brien-Fleming shaped (after week 9 and after week 18), their
    z boundaries converted to Student t on games - 1 degrees of freedom, as
    research/nfl_deep_weight_read.py converts its own;
  * look 1 stops for futility when the candidate is not ahead at all;
  * look 2 is FINAL. There is no third look and no third test.

It refuses: without R1's read, or when that read failed anything but
criterion 4 alone; before a look's date or the data's own last game + 78 h;
with too few games; a dirty tree; a pinned file that differs; a look already
read; look 2 after look 1 stopped the test; look 2 before look 1.
"""
import datetime
import json
import math
import os
import random
import subprocess
import sys
from multiprocessing import Pool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from research import nfl_coach_reg as RG          # noqa: E402

PREREG = os.path.join(RG.REG_DIR, "prereg_c4.json")
R1_READ = os.path.join(RG.REG_DIR, "read_r1.json")


def out_path(look):
    return os.path.join(RG.REG_DIR, f"confirm_c4_look{int(look)}.json")


def prereg():
    with open(PREREG) as fh:
        return json.load(fh)


def r1_allows(read):
    """True only when R1's read failed criterion 4 and passed the other four."""
    c = (read.get("verdict") or {}).get("criteria") or {}
    return (len(c) == 5 and c.get("4_late_state_wp") is False
            and all(v is True for k, v in c.items() if k != "4_late_state_wp"))


def refusal(look, now_utc=None, dirty=None, reg=None, r1=None, prior=None):
    """Why this look may not run, or None. `r1` (R1's read) and `prior` (look
    1's stored result, for look 2) are read from disk unless passed."""
    reg = reg or prereg()
    if look not in (1, 2):
        return "there are two looks and no third"
    if r1 is None:
        if not os.path.exists(R1_READ):
            return "R1 has not been read: this test exists only for an R1 that failed criterion 4 alone"
        with open(R1_READ) as fh:
            r1 = json.load(fh)
    if not r1_allows(r1):
        return "R1's read did not fail criterion 4 alone: no confirmatory test (a pass needs none; any other failure is final)"
    if look == 2:
        if prior is None:
            if not os.path.exists(out_path(1)):
                return "look 1 has not been read"
            with open(out_path(1)) as fh:
                prior = json.load(fh)
        if prior["decision"]["stop"]:
            return f"look 1 ended the test ({prior['decision']['outcome']}): there is no second look"
    lk = reg["looks"][look - 1]
    now = now_utc or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return RG.common_refusal(reg, now, lk["not_before_utc"], dirty, out_path(look))


def decide(look, mean, se, n, reg=None):
    """The registered decision at a look. `mean` is candidate minus control
    (negative is better), one number a game averaged; `se` its standard error."""
    reg = reg or prereg()
    lk = reg["looks"][look - 1]
    t = (-mean / se) if se > 0 else 0.0
    crit = RG.t_ppf(RG.norm_cdf(lk["z_boundary"]), max(1, n - 1))
    ub = -mean + RG.t_ppf(0.95, max(1, n - 1)) * se          # the largest gain the data do not exclude
    if t > crit:
        return {"outcome": "CONFIRMED", "stop": True, "t": round(t, 4), "t_critical": round(crit, 4),
                "gain_upper_95": round(ub, 5)}
    if look == 1 and mean >= 0:
        return {"outcome": "NOT CONFIRMED (futility: the candidate was not ahead at look 1)", "stop": True,
                "t": round(t, 4), "t_critical": round(crit, 4), "gain_upper_95": round(ub, 5)}
    if look == 2:
        return {"outcome": "NOT CONFIRMED (final look)", "stop": True, "t": round(t, 4),
                "t_critical": round(crit, 4), "gain_upper_95": round(ub, 5)}
    return {"outcome": "CONTINUE to the final look", "stop": False, "t": round(t, 4),
            "t_critical": round(crit, 4), "gain_upper_95": round(ub, 5)}


def _job(g):
    """One real game: the paired late-state log-loss difference, by R1's code."""
    from research import nfl_coach_eval as E
    imp = E.implied(g)
    if imp is None:
        return None
    T = E._tables(E.era_of(g["season"]))
    states = E.checkpoint_states(g)
    y = 1.0 if g["home_pts"] > g["away_pts"] else 0.0 if g["home_pts"] < g["away_pts"] else 0.5
    seed0 = int(g["id"].split("_")[0]) * 100000 + sum(ord(ch) for ch in g["id"])
    wp = {}
    for arm in ("control", "coach"):
        curve = E._W["curves"][E.era_of(g["season"])][arm]
        home = E._lvl_profile(T, E.level_for(curve, imp[0]))
        away = E._lvl_profile(T, E.level_for(curve, imp[1]))
        for cp in (300, 120):
            if cp not in states:
                continue
            rng = random.Random(seed0 + cp)
            w = t = 0
            for _ in range(E._W["playouts"]):
                res = E._play(arm, T, home, away, rng, start=states[cp], form_sd=E._W["form"][arm])
                m = res["h"]["pts"] - res["a"]["pts"]
                w += m > 0
                t += m == 0
            wp[(arm, cp)] = (w + 0.5 * t) / float(E._W["playouts"])
    ds = [E._ll(wp[("coach", cp)], y) - E._ll(wp[("control", cp)], y) for cp in (300, 120) if ("coach", cp) in wp]
    return (g["id"], sum(ds) / len(ds)) if ds else None


def main(argv):
    if len(argv) < 3 or "--look" not in argv:
        print(__doc__)
        return 2
    look = int(argv[argv.index("--look") + 1])
    reg = prereg()
    why = refusal(look, reg=reg)
    if why:
        print(f"REFUSED: {why}")
        return 1
    import nfl_coach_sim as C
    from research import nfl_coach_data as CD
    from research import nfl_coach_eval as E
    lk = reg["looks"][look - 1]
    games = [g for g in CD.load_season(argv[0], reg["season"], unseal=CD._UNSEAL_TOKEN, only_sealed=True,
                                       weeks=set(lk["weeks"])) if g["st"] == 2]
    last = max((g["date"] for g in games), default="")
    now = datetime.datetime.now(datetime.timezone.utc)
    if len(games) < lk["min_games"] or not last:
        print(f"REFUSED: {len(games)} games, the look needs {lk['min_games']}")
        return 1
    end = datetime.datetime.strptime(last, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc) + datetime.timedelta(hours=78)
    if now < end:
        print(f"REFUSED: the look's last game ({last}) + 78 h is {end:%Y-%m-%d %H:%M} UTC")
        return 1
    st = reg["settings"]
    curves = E.curves_for(C.TABLES_PATH, ["2025+"], n=st["level_curve_n"])
    with Pool(3, initializer=E._init, initargs=(C.TABLES_PATH, curves, st["form_sd"], 0, st["playouts"])) as pool:
        rows = [r for r in pool.imap(_job, games, chunksize=4) if r]
    d = [x for _id, x in rows]
    n = len(d)
    mean = sum(d) / n
    se = math.sqrt(sum((x - mean) ** 2 for x in d) / (n - 1) / n)
    dec = decide(look, mean, se, n, reg)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    out = {"registration": reg["block_sha256"], "commit": head, "look": look, "games": n, "last_game": last,
           "read_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mean_diff": round(mean, 6), "se": round(se, 6),
           "decision": dec, "r1_registered_verdict": "FAIL (unchanged by this test)"}
    with open(out_path(look), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
