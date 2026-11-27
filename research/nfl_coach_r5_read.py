"""THE registered R5 read: the trailing team's pull and the second unit as drawn
decisions (nfl_coach_sim_r5) on the 2028 regular season, read ONCE, on or after
2029-01-16 06:00 UTC, after R1's and R4's reads, from a clean tree at a commit
holding every pinned file:

  python3 -m research.nfl_coach_r5_read <dir: fresh play_by_play_2028.csv.gz, players.csv,
                                          injuries_2028.csv>

The rule is research/data/nfl_coach/prereg_r5.json (research/reports/nfl_coach_r5.md
section D). It refuses before its date or the window's last game + 48 h, on a
dirty tree, a block or pinned-file mismatch, a second read (read_r5.json), R1 or
R4 unread, or too few strategic pulled team-games. Nothing is tuned here.
"""
import datetime
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PREREG = os.path.join(ROOT, "research", "data", "nfl_coach", "prereg_r5.json")
OUT = os.path.join(ROOT, "research", "data", "nfl_coach", "read_r5.json")
EARLIER = {"R1": os.path.join(ROOT, "research", "data", "nfl_coach", "read_r1.json"),
           "R4": os.path.join(ROOT, "research", "data", "nfl_coach", "read_r4.json")}


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def prereg():
    with open(PREREG) as fh:
        return json.load(fh)


def _dirty():
    out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    return out.returncode != 0 or bool(out.stdout.strip())


def refusal(now_utc=None, dirty=None, reg=None, earlier_read=None, already=None):
    reg = reg or prereg()
    now = now_utc or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if now < reg["not_before_utc"]:
        return f"before the registered date ({reg['not_before_utc']})"
    if os.path.exists(OUT) if already is None else already:
        return "already read (read_r5.json exists): the test games are read once"
    for nm in reg["requires_reads"]:
        if not (os.path.exists(EARLIER[nm]) if earlier_read is None else earlier_read.get(nm)):
            return f"{nm}'s registered read has not been run and committed: it reads its games first"
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
    u, h, c = s["unit"], s["hazard"], s["cell"]
    crit = {"1_guards": s["guards_exit"] == 0,
            "2_second_unit_over_2se": u.get("se", 0) > 0 and u["gain_per_pulled_team_game"] > 2 * u["se"],
            # the hazard moves every drive of every team-game; its regime term targets one small corner,
            # so the whole-game gain is a guardrail (not worse by more than 2 SE), and the corner is the claim
            "3_hazard_not_worse": h.get("se", 0) > 0 and h["gain_per_team_game"] > -2 * h["se"]}
    # the registered SECONDARY (reported, never part of the verdict): does the post-2021 excess in the
    # trailing corner persist on fresh games? one-sided Poisson tail of the real count at R5's expectation
    secondary = {"corner_excess_persists": corner_p(c["pulls"], c["r5_expected"]) < 0.05,
                 "corner_p_one_sided": round(corner_p(c["pulls"], c["r5_expected"]), 5)}
    return {"pass": all(crit.values()), "criteria": crit, "secondary": secondary}


def corner_p(k, mu):
    """P(Poisson(mu) >= k)."""
    import math
    if k <= 0:
        return 1.0
    return max(0.0, 1.0 - sum(math.exp(-mu) * mu ** i / math.factorial(i) for i in range(k)))


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    reg = prereg()
    why = refusal(reg=reg)
    if why:
        print(f"REFUSED: {why}")
        return 1
    import nfl_coach_sim_r3 as E3
    import nfl_coach_sim_r4 as E4
    import nfl_coach_sim_r5 as E5
    from research import nfl_coach_r2_data as RD
    from research import nfl_coach_r3_qb as QB
    from research import nfl_coach_r4_data as XD
    from research import nfl_coach_r4_fit as F4
    from research import nfl_coach_r5_eval as EV5
    from research import nfl_coach_r5_fit as F5
    d = argv[0]
    pos = RD.positions(os.path.join(d, "players.csv"))
    cls = F4.make_cls(F4.reports(d, [w["season"] for w in reg["window"]]))
    P, X = E5.tables(), E4.tables()
    unit_rows, haz_rows, dates = [], [], []
    for win in reg["window"]:
        s, wk = win["season"], set(win["weeks"])
        sd_ = RD.load_season(d, s, unseal=RD._UNSEAL_TOKEN, use_cache=False)
        all_tgs = QB.team_games(sd_, pos)        # the whole season: the season shares leave one game out
        tgs = [q for q in all_tgs if q["week"] in wk]
        ex = XD.load_extra(d, s, unseal=XD._UNSEAL_TOKEN, use_cache=False)
        unit_rows += F5.unit_rows({s: all_tgs}, {s: sd_["players"]}, pos,
                                  lambda tg, ids, e: cls(tg, ids, e) if tg["week"] in wk else None, {s: ex}, (s,))
        tos = _timeouts_unsealed(d, s)
        haz_rows += [(s, [(a, b, c, 0.0, y) for a, b, c, y in F5.drive_states(tg, sd_["players"], pos, tos)])
                     for tg in tgs]
        dates += [q["game"]["date"] for q in tgs]
    last = max(dates, default="")
    now = datetime.datetime.now(datetime.timezone.utc)
    if not last or now < datetime.datetime.strptime(last, "%Y-%m-%d").replace(
            tzinfo=datetime.timezone.utc) + datetime.timedelta(hours=30 + 48):
        print(f"REFUSED: the window's last game ({last or 'none'}) + 48 h has not passed")
        return 1
    if len(unit_rows) < reg["min_strategic_pulled_team_games"]:
        print(f"REFUSED: {len(unit_rows)} strategic pulled team-games, the registration needs "
              f"{reg['min_strategic_pulled_team_games']}")
        return 1
    _d, unit = EV5.unit_score(unit_rows, P, X)
    haz = EV5.hazard_score(haz_rows, P)
    q3 = E3.tables().coef
    cell = {"pulls": F5.cell(haz_rows, q3, None)["pulls"], "r4_expected": F5.cell(haz_rows, q3, None)["expected"],
            "r5_expected": F5.cell(haz_rows, P.coef, P.spec)["expected"]}
    sc = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "guards_auto_nfl_coach_r5.py")],
                        cwd=ROOT, capture_output=True, text=True)
    s = {"unit": unit, "hazard": haz, "cell": cell, "guards_exit": sc.returncode}
    v = verdict(s)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    out = {"registration": reg["block_sha256"], "commit": head, "read_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "last_game": last, "summary": s, "verdict": v}
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print(json.dumps(out, indent=1, sort_keys=True))
    print("R5:", "PASS" if v["pass"] else "FAIL")
    return 0


def _timeouts_unsealed(d, s):
    """The window's timeouts. 2028 is past R1's seal season, so the seal rule
    (which drops every row from 2026 week 13 on) would drop all of it; the read
    is the one place sealed rows are opened, as R4's read opens them."""
    import csv
    import gzip
    from research import nfl_coach_data as CD
    out = {}
    with gzip.open(CD.path(d, s), "rt", newline="") as fh:
        rd = csv.reader(fh)
        ix = {h: j for j, h in enumerate(next(rd))}
        for raw in rd:
            if raw[ix["season_type"]] != "REG":
                continue
            try:
                out[(raw[ix["game_id"]], int(float(raw[ix["play_id"]])))] = int(float(raw[ix["posteam_timeouts_remaining"]]))
            except ValueError:
                pass
    return out


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
