"""THE registered R3 read: the quarterback's substitution (nfl_coach_sim_r3) on
2026 weeks 13-18, read ONCE, on or after 2027-01-06 06:00 UTC, after R1's own
read of those games, from a clean tree at a commit holding every pinned file:

  python3 -m research.nfl_coach_r3_read <dir with a fresh play_by_play_2026.csv.gz and players.csv>

The rule is research/data/nfl_coach/prereg_r3.json (written before any test game
was read; research/reports/nfl_coach_r3.md section D is its prose). It refuses
before its date or before the window's last game + 48 h, on a dirty tree, on a
block or pinned-file mismatch, a second read (read_r3.json exists), R1 not yet
read (read_r1.json absent), or too few team-games. Nothing is tuned here.
Any change to R3 (or to the R1/R2 files it plays on) is a pinned-file mismatch,
so a modified R3 cannot be read on these games: it needs a new registration on
fresh games.
"""
import datetime
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PREREG = os.path.join(ROOT, "research", "data", "nfl_coach", "prereg_r3.json")
OUT = os.path.join(ROOT, "research", "data", "nfl_coach", "read_r3.json")
R1_READ = os.path.join(ROOT, "research", "data", "nfl_coach", "read_r1.json")


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def prereg():
    with open(PREREG) as fh:
        return json.load(fh)


def _dirty():
    out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    return out.returncode != 0 or bool(out.stdout.strip())


def refusal(now_utc=None, dirty=None, reg=None, r1_read=None, already=None):
    reg = reg or prereg()
    now = now_utc or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if now < reg["not_before_utc"]:
        return f"before the registered date ({reg['not_before_utc']})"
    if os.path.exists(OUT) if already is None else already:
        return "already read (read_r3.json exists): the test games are read once"
    if reg.get("requires_r1_read") and not (os.path.exists(R1_READ) if r1_read is None else r1_read):
        return "R1's registered read has not been run and committed (read_r1.json): R1 reads these games first"
    if _dirty() if dirty is None else dirty:
        return "dirty tree: commit or remove every change first"
    body = json.dumps({k: v for k, v in reg.items() if k != "block_sha256"}, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(body.encode()).hexdigest() != reg["block_sha256"]:
        return "the registration block does not match its own hash"
    for rel, hh in reg["files"].items():
        if _sha(rel) != hh:
            return f"{rel} differs from its registered sha256"
    return None


def verdict(s):
    """The four registered criteria (all must hold)."""
    st, re_ = s["stage1"], s["realism"]
    d, y = re_["starter_dropbacks_crps"], re_["starter_pass_yards_crps"]
    c = {"1_guards": s["guards_exit"] == 0,
         "2_hazard_gain_over_2se": st["se"] > 0 and st["gain_per_team_game"] > 2.0 * st["se"],
         "3_starter_dropbacks_crps_better_over_2se": d["se"] > 0 and d["r3_minus_r2_per_team_game"] < -2.0 * d["se"],
         "4_starter_pass_yards_crps_not_worse": y["r3_minus_r2_per_team_game"] <= y["se"]}
    return {"pass": all(c.values()), "criteria": c}


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    reg = prereg()
    why = refusal(reg=reg)
    if why:
        print(f"REFUSED: {why}")
        return 1
    import nfl_coach_sim as C
    import nfl_coach_sim_r3 as E3
    from research import nfl_coach_eval as CE
    from research import nfl_coach_r2_data as RD
    from research import nfl_coach_r2_eval as EV2
    from research import nfl_coach_r3_eval as EV3
    from research import nfl_coach_r3_qb as QB
    win, st = reg["window"][0], reg["settings"]
    weeks = set(win["weeks"])
    sd_ = RD.load_season(argv[0], win["season"], unseal=RD._UNSEAL_TOKEN, use_cache=False)
    pos = RD.positions(os.path.join(argv[0], "players.csv"))
    qtgs = [q for q in QB.team_games(sd_, pos) if q["week"] in weeks]
    prs = [pr for pr in EV3.pairs(sd_, pos) if pr[0][0]["week"] in weeks]
    now = datetime.datetime.now(datetime.timezone.utc)
    last = max((q["game"]["date"] for q in qtgs), default="")
    if not last or now < datetime.datetime.strptime(last, "%Y-%m-%d").replace(
            tzinfo=datetime.timezone.utc) + datetime.timedelta(hours=30 + 48):
        print(f"REFUSED: the window's last game ({last or 'none'}) + 48 h has not passed")
        return 1
    if len(qtgs) < reg["min_team_games"]:
        print(f"REFUSED: {len(qtgs)} team-games in the window, the registration needs {reg['min_team_games']}")
        return 1
    s1 = EV3.summarize1(EV3.stage1(qtgs, E3.tables()))
    s1["pulls"] = sum(q["pull"] is not None for q in qtgs)
    cv = EV2.curves(C.TABLES_PATH, [CE.era_of(win["season"])], st["level_curve_n"])
    re_ = EV3.summarize_realism(EV3.run(prs, st["sims"], cv, workers=st.get("workers", 4)))
    sc = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "guards_auto_nfl_coach_r3.py")],
                        cwd=ROOT, capture_output=True, text=True)
    s = {"team_games": len(qtgs), "stage1": s1, "realism": re_, "guards_exit": sc.returncode}
    v = verdict(s)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    out = {"registration": reg["block_sha256"], "commit": head, "read_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "last_game": last, "summary": s, "verdict": v}
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print(json.dumps(out, indent=1, sort_keys=True))
    print("R3:", "PASS" if v["pass"] else "FAIL")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
