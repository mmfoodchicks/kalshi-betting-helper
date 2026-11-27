"""THE registered R1 read: the experimental coaching engine (nfl_coach_sim)
against the control (nfl_deep_sim on the same tables) on the sealed games,
2026 weeks 13-18. Run ONCE, on or after 2027-01-06 06:00 UTC, from a clean
tree at a commit that holds the registration unchanged.

  python3 -m research.nfl_coach_r1_read <dir with a fresh play_by_play_2026.csv.gz>

The rule is research/data/nfl_coach/prereg_r1.json (written before any sealed
game was read; research/reports/nfl_coach_r1.md section 6 is its prose). This
file is pinned there by sha256, with the engine, the tables, the measuring
code and the loader. It refuses:
  * before the date, or before the window's last game + 48 h by the data's own
    game dates, or with fewer games than the window should hold;
  * a dirty tree, or any pinned file that differs from its registered hash;
  * a second read (research/data/nfl_coach/read_r1.json exists).
Nothing is tuned here: sims, play-outs, seeds, the clip and the level anchor
are the registration's. The verdict is `verdict`, five criteria, all must pass.

A PASS makes R1 eligible for the next step (a shadow bucket of its own is a
production change needing the owner's word). A FAIL is final for this
candidate: a revised engine is judged on games after week 18, never these.
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

PREREG = os.path.join(ROOT, "research", "data", "nfl_coach", "prereg_r1.json")
OUT = os.path.join(ROOT, "research", "data", "nfl_coach", "read_r1.json")
FIGS = ("q2_snaps", "q2_pts", "q4_snaps", "q4_pts")


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def prereg():
    with open(PREREG) as fh:
        return json.load(fh)


def _dirty():
    out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    return out.returncode != 0 or bool(out.stdout.strip())


def refusal(now_utc=None, dirty=None, reg=None):
    """Why the read may not run now, or None. `now_utc` / `dirty` are passed
    by the guards; the real read takes the clock and git."""
    reg = reg or prereg()
    now = now_utc or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if now < reg["not_before_utc"]:
        return f"before the registered date ({reg['not_before_utc']})"
    if os.path.exists(OUT):
        return "already read (research/data/nfl_coach/read_r1.json exists): the sealed games are read once"
    if _dirty() if dirty is None else dirty:
        return "dirty tree: commit or remove every change first"
    body = json.dumps({k: v for k, v in reg.items() if k != "block_sha256"}, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(body.encode()).hexdigest() != reg["block_sha256"]:
        return "the registration block does not match its own hash"
    for rel, h in reg["files"].items():
        if _sha(rel) != h:
            return f"{rel} differs from its registered sha256"
    return None


def verdict(s, scenarios_pass):
    """The five registered criteria on nfl_coach_eval.summarize's output."""
    n = s["games"]
    real, ctl, cand = s["real"], s["control"], s["coach"]
    c = {"1_scenarios": bool(scenarios_pass)}
    # 2. each inside-2:00 figure closer to the real one than the control, by more than the real figure's SE
    fid = {}
    for k in FIGS:
        m, se = real[k]
        fid[k] = abs(ctl[k] - m) - abs(cand[k] - m) > se
    c["2_fidelity"] = all(fid.values())
    # 3. must not worsen, each within one SE of the real statistic
    p = real["home_win"]
    ses = {"total": real["total"][1], "ot": real["ot"][1],
           "margin_resid_sd": real["margin_resid_sd"] / math.sqrt(2.0 * n),
           "total_resid_sd": real["total_resid_sd"] / math.sqrt(2.0 * n),
           "home_win": math.sqrt(max(1e-9, p * (1 - p)) / n)}
    lvl = {}
    for k, se in ses.items():
        m = real[k][0] if isinstance(real[k], list) else real[k]
        lvl[k] = abs(cand[k] - m) <= abs(ctl[k] - m) + se
    c["3_level"] = all(lvl.values())
    # 4. late-state win probability: paired log loss, one number a game, better by more than 2 SE
    w = s["wp_late"]
    c["4_late_state_wp"] = w["se"] > 0 and w["diff"] < -2.0 * w["se"]
    # 5. pre-game: not worse by more than 1 SE (moneyline log loss and total CRPS)
    d, se = s["pregame_logloss_diff"]
    d2, se2 = s["total_crps_diff"]
    c["5_pregame"] = d <= se and d2 <= se2
    return {"pass": all(c.values()), "criteria": c, "fidelity": fid, "level": lvl}


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
    from research import nfl_coach_data as CD
    from research import nfl_coach_eval as E
    win = reg["window"]
    games = [g for g in CD.load_season(argv[0], win["season"], unseal=CD._UNSEAL_TOKEN, only_sealed=True,
                                       weeks=set(win["weeks"])) if g["st"] == 2]
    last = max((g["date"] for g in games), default="")
    now = datetime.datetime.now(datetime.timezone.utc)
    if len(games) < win["min_games"] or not last:
        print(f"REFUSED: {len(games)} games in the window, the registration needs {win['min_games']}")
        return 1
    # a game dated D (Eastern) has ended by D+1 06:00 UTC; the read waits 48 h past that
    end = datetime.datetime.strptime(last, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc) + datetime.timedelta(hours=30 + 48)
    if now < end:
        print(f"REFUSED: the window's last game ({last}) + 48 h is {end:%Y-%m-%d %H:%M} UTC")
        return 1
    sc = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "guards_auto_nfl_coach.py")],
                        cwd=ROOT, capture_output=True, text=True)
    st = reg["settings"]
    curves = E.curves_for(C.TABLES_PATH, ["2025+"], n=st["level_curve_n"])
    rows = E.run_games(games, C.TABLES_PATH, curves, st["form_sd"], st["sims"], st["playouts"])
    s = E.summarize(rows)
    v = verdict(s, sc.returncode == 0)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    out = {"registration": reg["block_sha256"], "commit": head, "read_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "games": len(rows), "last_game": last, "summary": s, "verdict": v,
           "scenario_guards_exit": sc.returncode}
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print(json.dumps(out, indent=1, sort_keys=True))
    print("R1:", "PASS" if v["pass"] else "FAIL")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
