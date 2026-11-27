"""R1's read, reported as three questions. It CHANGES NOTHING: the registered
verdict (all five criteria, research/data/nfl_coach/prereg_r1.json) is read
from research/data/nfl_coach/read_r1.json and stated first, unchanged. Under
it, the same five registered criteria are grouped by what they measure, so a
failure to show a better win probability is not read as a failure to play the
end of a half like a football game, or the other way round.

  python3 -m research.nfl_coach_r1_report [path to read_r1.json]

  realism                    criteria 1-3  scenario tests, the four inside-2:00
                                           figures, the level not worse
  late-state win probability criterion 4
  pre-game non-inferiority   criterion 5

No criterion is recomputed, loosened or re-weighted here; a group passes when
every registered criterion in it passed.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
READ = os.path.join(ROOT, "research", "data", "nfl_coach", "read_r1.json")
GROUPS = (("realism (criteria 1-3)", ("1_scenarios", "2_fidelity", "3_level")),
          ("late-state win probability (criterion 4)", ("4_late_state_wp",)),
          ("pre-game non-inferiority (criterion 5)", ("5_pregame",)))


def three(read):
    """{"registered": PASS/FAIL, "groups": [(name, PASS/FAIL, {criterion: bool})],
    "only_criterion_4_failed": bool} from the read's own verdict."""
    v = read["verdict"]
    c = v["criteria"]
    groups = [(name, "PASS" if all(c[k] for k in keys) else "FAIL", {k: bool(c[k]) for k in keys})
              for name, keys in GROUPS]
    return {"registered": "PASS" if v["pass"] else "FAIL", "groups": groups,
            "only_criterion_4_failed": (not c["4_late_state_wp"]) and all(c[k] for k in c if k != "4_late_state_wp")}


def text(read):
    t = three(read)
    s = read.get("summary") or {}
    L = [f"R1 REGISTERED VERDICT: {t['registered']}  (all five criteria must hold; registration "
         f"{str(read.get('registration'))[:8]}, {read.get('games')} games, read {read.get('read_utc')})", ""]
    for name, res, parts in t["groups"]:
        L.append(f"  {res:4}  {name}   " + ", ".join(f"{k} {'pass' if ok else 'fail'}" for k, ok in parts.items()))
    w = s.get("wp_late")
    if w:
        L.append(f"\n  late-state log loss, candidate minus control: {w['diff']:+.5f} +/- {w['se']:.5f} ({w['n']} games)")
    if s.get("pregame_logloss_diff"):
        d, se = s["pregame_logloss_diff"]
        d2, se2 = s["total_crps_diff"]
        L.append(f"  pre-game log loss {d:+.5f} +/- {se:.5f}; total CRPS {d2:+.4f} +/- {se2:.4f}")
    L.append("")
    if t["only_criterion_4_failed"]:
        L.append("  Only criterion 4 failed: the confirmatory test (research/nfl_coach_c4_confirm.py) may run on its dates.")
    elif t["registered"] == "FAIL":
        L.append("  More than criterion 4 failed, or another criterion did: no confirmatory test. The FAIL is final.")
    L.append("  The registered verdict above is the record; the grouping does not change it.")
    return "\n".join(L)


def main(argv):
    p = argv[0] if argv else READ
    if not os.path.exists(p):
        print("R1 has not been read (no read_r1.json).")
        return 1
    with open(p) as fh:
        print(text(json.load(fh)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
