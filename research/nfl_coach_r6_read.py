"""THE registered R6 reads: the selectively drifting league (nfl_coach_sim_r6) on the
2027 regular season (primary) and the 2028 regular season (replication), each read
ONCE from a clean tree at a commit holding every pinned file:

  python3 -m research.nfl_coach_r6_read primary <dir: fresh play_by_play_2027.csv.gz, players.csv>
  python3 -m research.nfl_coach_r6_read replication <dir: fresh play_by_play_2028.csv.gz, players.csv>

The rule is research/data/nfl_coach/prereg_r6.json (research/reports/nfl_coach_r6.md
section D). Every statistic is a likelihood of what happened at the FROZEN tables
(nothing is refitted, the replication included). Refuses before its date or the
window's last game + 48 h, on a dirty tree, a block or pinned-file mismatch, a
second read, an earlier read missing (R1's for the primary; R6's primary for the
replication), or too few games.
"""
import datetime
import hashlib
import json
import math
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PREREG = os.path.join(ROOT, "research", "data", "nfl_coach", "prereg_r6.json")
OUT = {"primary": os.path.join(ROOT, "research", "data", "nfl_coach", "read_r6_2027.json"),
       "replication": os.path.join(ROOT, "research", "data", "nfl_coach", "read_r6_2028.json")}
EARLIER = {"R1": os.path.join(ROOT, "research", "data", "nfl_coach", "read_r1.json"),
           "R6-primary": OUT["primary"]}


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def prereg():
    with open(PREREG) as fh:
        return json.load(fh)


def _dirty():
    out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    return out.returncode != 0 or bool(out.stdout.strip())


def refusal(which, now_utc=None, dirty=None, reg=None, earlier_read=None, already=None):
    reg = reg or prereg()
    if which not in reg["windows"]:
        return f"no such window: {which}"
    w = reg["windows"][which]
    now = now_utc or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if now < w["not_before_utc"]:
        return f"before the registered date ({w['not_before_utc']})"
    if os.path.exists(OUT[which]) if already is None else already:
        return f"already read ({os.path.basename(OUT[which])} exists): the games are read once"
    for nm in w["requires_reads"]:
        if not (os.path.exists(EARLIER[nm]) if earlier_read is None else earlier_read.get(nm)):
            return f"{nm}'s registered read has not been run and committed: it reads first"
    if _dirty() if dirty is None else dirty:
        return "dirty tree: commit or remove every change first"
    body = json.dumps({k: v for k, v in reg.items() if k != "block_sha256"}, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(body.encode()).hexdigest() != reg["block_sha256"]:
        return "the registration block does not match its own hash"
    for rel, hh in reg["files"].items():
        if _sha(rel) != hh:
            return f"{rel} differs from its registered sha256"
    return None


def summ(d):
    v = list(d.values())
    n = len(v)
    if n < 2:
        return {"games": n, "gain_per_game": 0.0, "se": 0.0, "z": None}
    m = sum(v) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / (n - 1))
    se = sd / math.sqrt(n)
    return {"games": n, "gain_per_game": m, "se": se, "z": m / se if se else None}


def verdict(s):
    """The registered criteria (all must hold)."""
    p, comps = s["pooled"], s["components"]
    c = {"1_guards": s["guards_exit"] == 0,
         "2_pooled_over_2se": p["se"] > 0 and p["gain_per_game"] > 2 * p["se"],
         "3_no_component_below_minus_1se": all(comps[k]["gain_per_game"] > -comps[k]["se"]
                                               for k in ("clock", "rb", "pull"))}
    return {"pass": all(c.values()), "criteria": c}


def main(argv):
    if len(argv) < 2 or argv[0] not in ("primary", "replication"):
        print(__doc__)
        return 2
    which, d = argv[0], argv[1]
    reg = prereg()
    why = refusal(which, reg=reg)
    if why:
        print(f"REFUSED: {why}")
        return 1
    from research import nfl_coach_evo as EVO
    from research import nfl_coach_r2_data as R
    from research import nfl_coach_r3_qb as QB
    from research import nfl_coach_r6_fit as F6
    w = reg["windows"][which]
    tab = json.load(open(os.path.join(ROOT, "research", "data", "nfl_coach_r6_tables.json")))
    pos = R.positions(os.path.join(d, "players.csv"))
    s, wk = w["season"], set(w["weeks"])
    sd_ = R.load_season(d, s, unseal=R._UNSEAL_TOKEN, use_cache=False)
    sd_["games"] = [g for g in sd_["games"] if g["st"] == 2 and g["week"] in wk]
    dates = [g["date"] for g in sd_["games"] if g.get("date")]
    last = max(dates, default="")
    now = datetime.datetime.now(datetime.timezone.utc)
    if not last or now < datetime.datetime.strptime(last, "%Y-%m-%d").replace(
            tzinfo=datetime.timezone.utc) + datetime.timedelta(hours=30 + 48):
        print(f"REFUSED: the window's last game ({last or 'none'}) + 48 h has not passed")
        return 1
    if len(sd_["games"]) < reg["min_games"]:
        print(f"REFUSED: {len(sd_['games'])} games, the registration needs {reg['min_games']}")
        return 1
    tgs = QB.team_games(sd_, pos)
    pr_S = [(tg["gid"], [(a, b, 3, 0.0, y) for a, b, y, _p in tg["seq"]]) for tg in tgs]
    gs, tot = F6.window_gains(tab, F6.clock_rows(sd_), EVO.cell_rows(sd_, pos)["T4_rb_target"], pr_S)
    sc = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "guards_auto_nfl_coach_r6.py")],
                        cwd=ROOT, capture_output=True, text=True)
    summary = {"pooled": summ(tot), "components": {k: summ(v) for k, v in gs.items()}, "guards_exit": sc.returncode}
    v = verdict(summary)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    out = {"registration": reg["block_sha256"], "window": which, "commit": head,
           "read_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "last_game": last, "summary": summary, "verdict": v}
    with open(OUT[which], "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print(json.dumps(out, indent=1, sort_keys=True))
    print(f"R6 {which}:", "PASS" if v["pass"] else "FAIL")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
