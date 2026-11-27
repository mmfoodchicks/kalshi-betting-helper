"""THE registered R2 reads (research/reports/nfl_coach_r2_evidence.md section C).
Each hypothesis is read ONCE, from a clean tree at a commit that holds the
registration and every pinned file unchanged:

  python3 -m research.nfl_coach_r2_read H1 <dir with fresh play_by_play_2026.csv.gz>
  python3 -m research.nfl_coach_r2_read H2 <dir with fresh play_by_play_2026 and _2027.csv.gz>

  H1  who gets the ball by situation (nfl_coach_sim_r2): 2026 weeks 13-18, on or
      after 2027-01-06 06:00 UTC, and only after R1's own read of those games
      (research/data/nfl_coach/read_r1.json in the tree): R1's statistics are
      game-level and frozen by sha256, R2's are who-got-the-ball and call
      statistics, so reading them second cannot move R1, and R1's games are
      never seen by anyone before R1's read.
  H2  in-game adaptation of the call (research/nfl_coach_r2_adapt.py, decision
      level only): 2026 weeks 13-18 and 2027 weeks 1-18, on or after
      2028-01-13 06:00 UTC, after R1's read.

The rule is research/data/nfl_coach/prereg_r2.json (written before any test
game was read). It refuses: before its date, or before the window's last game
+ 48 h by the data's own dates; a dirty tree; a registration whose block hash
or any pinned file differs; a hypothesis already read (its read file exists);
R1 not yet read; fewer team-games than registered. Nothing is tuned here.
A FAIL is final for that candidate: a revised one is judged on later games.
"""
import datetime
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PREREG = os.path.join(ROOT, "research", "data", "nfl_coach", "prereg_r2.json")
R1_READ = os.path.join(ROOT, "research", "data", "nfl_coach", "read_r1.json")


def out_path(h):
    return os.path.join(ROOT, "research", "data", "nfl_coach", f"read_r2_{h}.json")


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def prereg():
    with open(PREREG) as fh:
        return json.load(fh)


def _dirty():
    out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    return out.returncode != 0 or bool(out.stdout.strip())


def refusal(h, now_utc=None, dirty=None, reg=None, r1_read=None, already=None):
    """Why hypothesis `h` may not be read now, or None. The guards pass the
    clock, the tree state and the two file checks; the real read takes them."""
    reg = reg or prereg()
    hyp = reg["hypotheses"].get(h)
    if hyp is None:
        return f"no hypothesis {h!r} is registered"
    now = now_utc or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if now < hyp["not_before_utc"]:
        return f"before the registered date ({hyp['not_before_utc']})"
    if (os.path.exists(out_path(h)) if already is None else already):
        return f"already read ({os.path.basename(out_path(h))} exists): the test games are read once"
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


def verdict_h1(s):
    """H1's four registered criteria (all must hold) on `measure_h1`'s summary."""
    st, re_ = s["stage1"], s["realism"]
    c = {"1_guards": s["guards_exit"] == 0,
         "2_joint_gain_over_2se": st["both"]["se"] > 0 and st["both"]["gain_per_team_game"] > 2.0 * st["both"]["se"],
         "3_carries_positive_targets_not_worse": st["car"]["gain_per_team_game"] > 0
         and st["tgt"]["gain_per_team_game"] > -st["tgt"]["se"],
         "4_realism_not_worse": all(re_[k]["r2_minus_static_per_team_game"] <= re_[k]["se"]
                                    for k in ("carries_crps", "targets_crps", "td_logscore"))}
    return {"pass": all(c.values()), "criteria": c}


def verdict_h2(s):
    c = {"1_guards": s["guards_exit"] == 0,
         "2_gain_over_2se": s["se"] > 0 and s["gain_per_team_game"] > 2.0 * s["se"]}
    return {"pass": all(c.values()), "criteria": c}


def _load(dirname, season):
    from research import nfl_coach_r2_data as RD
    # every row of the season (the as-of shares need the unsealed weeks before the window)
    return RD.load_season(dirname, season, unseal=RD._UNSEAL_TOKEN, use_cache=False)


def _last_ok(dates, now):
    last = max(dates, default="")
    if not last:
        return last, False
    end = datetime.datetime.strptime(last, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc) + \
        datetime.timedelta(hours=30 + 48)
    return last, now >= end


def measure_h1(dirname, hyp):
    import nfl_coach_sim as C
    import nfl_coach_sim_r2 as E2
    from research import nfl_coach_r2_data as RD
    from research import nfl_coach_r2_eval as EV
    st = hyp["settings"]
    win = hyp["window"][0]
    sd = _load(dirname, win["season"])
    pos = RD.positions(os.path.join(dirname, "players.csv"))
    tgs = [t for t in EV.team_games(sd, pos) if t["week"] in set(win["weeks"])]
    dates = [t["game"]["date"] for t in tgs]
    U = E2.tables()
    s1 = EV.summarize1(EV.stage1(tgs, U))
    cv = EV.curves(C.TABLES_PATH, ["2025+"], st["level_curve_n"])
    re_ = EV.summarize_realism(EV.run_realism(tgs, st["sims"], cv, workers=st.get("workers", 4)))
    return {"team_games": len(tgs), "stage1": s1, "stage1_core": EV.summarize1(EV.stage1(tgs, U, core=True)),
            "realism": re_}, dates


def measure_h2(dirname, hyp):
    from research import nfl_coach_r2_adapt as AD
    co = json.load(open(AD.OUT))
    tg = []
    dates = []
    for win in hyp["window"]:
        sd = _load(dirname, win["season"])
        wk = set(win["weeks"])
        for key, game in AD.rows(sd):
            if key[1] in wk:
                tg.append((key, game))
        dates += [g["date"] for g in sd["games"] if g["st"] == 2 and g["week"] in wk]
    s = AD.summarize(AD.score(tg, co["coef_baseline"], co["coef_candidate"]))
    return s, dates


def main(argv):
    if len(argv) < 2 or argv[0] not in ("H1", "H2"):
        print(__doc__)
        return 2
    h, dirname = argv[0], argv[1]
    reg = prereg()
    why = refusal(h, reg=reg)
    if why:
        print(f"REFUSED: {why}")
        return 1
    hyp = reg["hypotheses"][h]
    now = datetime.datetime.now(datetime.timezone.utc)
    s, dates = (measure_h1 if h == "H1" else measure_h2)(dirname, hyp)
    last, ok = _last_ok(dates, now)
    if not ok:
        print(f"REFUSED: the window's last game ({last or 'none'}) + 48 h has not passed")
        return 1
    if s["team_games"] < hyp["min_team_games"]:
        print(f"REFUSED: {s['team_games']} team-games in the window, the registration needs {hyp['min_team_games']}")
        return 1
    sc = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "guards_auto_nfl_coach_r2.py")],
                        cwd=ROOT, capture_output=True, text=True)
    s["guards_exit"] = sc.returncode
    v = verdict_h1(s) if h == "H1" else verdict_h2(s)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    out = {"hypothesis": h, "registration": reg["block_sha256"], "commit": head,
           "read_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "last_game": last, "summary": s, "verdict": v}
    with open(out_path(h), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print(json.dumps(out, indent=1, sort_keys=True))
    print(f"R2 {h}:", "PASS" if v["pass"] else "FAIL")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
