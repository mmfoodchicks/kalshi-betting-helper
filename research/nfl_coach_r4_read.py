"""THE registered R4 read: personnel-aware decisions after a quarterback
substitution (nfl_coach_sim_r4) on 2026 weeks 13-18 and the 2027 regular season,
read ONCE, on or after 2028-01-13 06:00 UTC, after R1's read, from a clean tree
at a commit holding every pinned file:

  python3 -m research.nfl_coach_r4_read <dir: fresh play_by_play_2026/2027.csv.gz, players.csv,
                                          injuries_2026.csv, injuries_2027.csv>

The rule is research/data/nfl_coach/prereg_r4.json (research/reports/nfl_coach_r4.md
section D). It refuses before its date or the window's last game + 48 h, on a
dirty tree, a block or pinned-file mismatch, a second read (read_r4.json), R1
unread, or too few pulled team-games. Nothing is tuned here.
"""
import datetime
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PREREG = os.path.join(ROOT, "research", "data", "nfl_coach", "prereg_r4.json")
OUT = os.path.join(ROOT, "research", "data", "nfl_coach", "read_r4.json")
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
        return "already read (read_r4.json exists): the test games are read once"
    if reg.get("requires_r1_read") and not (os.path.exists(R1_READ) if r1_read is None else r1_read):
        return "R1's registered read has not been run and committed (read_r1.json): R1 reads its games first"
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
    """The registered criteria (all must hold)."""
    d = s["decision"]
    re_ = s["realism"]
    c = {"1_guards": s["guards_exit"] == 0,
         "2_joint_gain_over_2se": d["joint"]["se"] > 0 and d["joint"]["gain_per_pulled_team_game"] > 2 * d["joint"]["se"],
         "3_no_component_worse_by_1se": all(d[k]["gain_per_pulled_team_game"] > -d[k]["se"]
                                            for k in ("personnel", "call", "comp")),
         # a guardrail, not a claim: R4 moves only the ~15% of team-games with a pull, so its expected
         # team-game difference is ~0 (development: |z| < 0.8 both seasons) and a 1-SE bound would fail
         # about 30% of the time with no harm done; 2 SE fails about 5%
         "4_realism_not_worse": all(re_[k]["r4_minus_r3_per_team_game"] <= 2 * re_[k]["se"]
                                    for k in ("rb1_carries_crps", "top2_targets_crps"))}
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
    import nfl_coach_sim_r4 as E4
    from research import nfl_coach_r2_data as RD
    from research import nfl_coach_r2_eval as EV2
    from research import nfl_coach_r3_eval as EV3
    from research import nfl_coach_r3_qb as QB
    from research import nfl_coach_r4_data as XD
    from research import nfl_coach_r4_eval as EV4
    from research import nfl_coach_r4_fit as F
    d, st = argv[0], reg["settings"]
    pos = RD.positions(os.path.join(d, "players.csv"))
    cls = F.make_cls(F.reports(d, [w["season"] for w in reg["window"]]))
    rows, pairs, dates = [], [], []
    for win in reg["window"]:
        s, wk = win["season"], set(win["weeks"])
        sd_ = RD.load_season(d, s, unseal=RD._UNSEAL_TOKEN, use_cache=False)
        all_tgs = QB.team_games(sd_, pos)            # the whole season: the as-of roles need its early weeks
        tgs = [q for q in all_tgs if q["week"] in wk]
        ex = XD.load_extra(d, s, unseal=XD._UNSEAL_TOKEN, use_cache=False)
        pulled = [g for g in EV4.collect_with_calls({s: all_tgs}, {s: sd_["players"]}, {s: ex}, pos, cls)
                  if g["tg"]["week"] in wk]
        rows += EV4.decision(pulled, E4.tables())
        pairs += [pr for pr in EV3.pairs(sd_, pos) if pr[0][0]["week"] in wk]
        dates += [q["game"]["date"] for q in tgs]
    last = max(dates, default="")
    now = datetime.datetime.now(datetime.timezone.utc)
    if not last or now < datetime.datetime.strptime(last, "%Y-%m-%d").replace(
            tzinfo=datetime.timezone.utc) + datetime.timedelta(hours=30 + 48):
        print(f"REFUSED: the window's last game ({last or 'none'}) + 48 h has not passed")
        return 1
    if len(rows) < reg["min_pulled_team_games"]:
        print(f"REFUSED: {len(rows)} pulled team-games, the registration needs {reg['min_pulled_team_games']}")
        return 1
    dec = EV4.summarize(rows)
    cv = EV2.curves(C.TABLES_PATH, ["2025+"], st["level_curve_n"])
    rea = EV4.realism(pairs, st["sims"], cv, workers=st.get("workers", 4))
    sc = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "guards_auto_nfl_coach_r4.py")],
                        cwd=ROOT, capture_output=True, text=True)
    s = {"decision": dec, "realism": rea, "guards_exit": sc.returncode}
    v = verdict(s)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    out = {"registration": reg["block_sha256"], "commit": head, "read_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "last_game": last, "summary": s, "verdict": v}
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print(json.dumps(out, indent=1, sort_keys=True))
    print("R4:", "PASS" if v["pass"] else "FAIL")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
