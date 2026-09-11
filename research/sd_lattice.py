"""Are the scores the showdown board feeds its tie machinery reachable under
DraftKings scoring?

Showdown's headline column is a SOLE-win probability, and that is decided by
how often two lineups land on the same number. The tie arithmetic is careful
and correct (Stage 3B of the classic pass). What it is fed is not: the legacy
simulator pins each player's mean by multiplying his whole point array by
proj/raw, shifts a defense by a floating amount, and rounds to two decimals.
DraftKings' offence lives on 0.02 -- 0.04 a passing yard, 0.1 a rushing or
receiving yard, whole receptions, touchdowns and bonuses -- so a 0.01 grid is
twice as fine as anything the house can print, and exact ties come out about
half as likely as they really are.

    python3 -m research.sd_lattice <dir with the cached Sleeper feeds>
"""
import collections
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")
SEASON, WEEK, WORLDS = "2026", 1, 4000
DK_STEP = 0.02          # the coarsest step DK offensive scoring can move


def _prov(**extra):
    from research import provenance
    return provenance.stamp(**extra)


def run(feed_dir, log=print):
    import nfl_dfs_sim as S
    raw = json.load(open(os.path.join(feed_dir, f"proj_{SEASON}_{WEEK}.json")))
    dfn = json.load(open(os.path.join(feed_dir, f"proj_{SEASON}_{WEEK}_def.json")))
    kck = json.load(open(os.path.join(feed_dir, f"proj_{SEASON}_{WEEK}_k.json")))
    S._get = lambda url: (dfn if "DEF" in url else kck if "position[]=K" in url else raw)
    S._cache.clear()
    pool = S.player_pool(WEEK, n=WORLDS, season=SEASON, model="legacy")
    by = collections.defaultdict(list)
    for p in pool.values():
        by[(p.get("pos") or "?").upper()].append(np.asarray(p["arr"], dtype=np.float64))
    rows, tot_bad, tot_n = [], 0, 0
    for pos in sorted(by):
        A = np.concatenate(by[pos])
        r = np.abs(A / DK_STEP - np.rint(A / DK_STEP))
        bad = int((r > 1e-9).sum())
        tot_bad += bad
        tot_n += A.size
        rows.append({"pos": pos, "players": len(by[pos]), "samples": int(A.size),
                     "off_lattice": bad, "off_lattice_pct": round(100.0 * bad / A.size, 2),
                     "worst_residual_steps": round(float(r.max()), 6)})
        log(f"[SDL] {pos:4s} {bad:>9,} of {A.size:>9,} off the lattice "
            f"({100.0 * bad / A.size:5.2f}%), worst residual {r.max():.3f} steps")
    A = np.concatenate([np.asarray(p["arr"], dtype=np.float64) for p in pool.values()])
    out = {"meta": {"stage": "showdown DK-lattice audit (a measurement, not a fix)",
                    "season": SEASON, "week": WEEK, "worlds": WORLDS,
                    "model": "legacy", "dk_step": DK_STEP,
                    "provenance": _prov(model="legacy", worlds=WORLDS)},
           "by_position": rows,
           "total": {"samples": int(tot_n), "off_lattice": int(tot_bad),
                     "off_lattice_pct": round(100.0 * tot_bad / max(tot_n, 1), 2),
                     "distinct_values": int(len(np.unique(np.round(A, 6))))},
           "mechanism": ["skill players: arr = [x * (proj / raw) for x in arr], a per-player "
                         "floating multiply, then round(x, 2)",
                         "defenses: arr = [round(max(-4.0, x + shift), 2)], a floating additive "
                         "shift to the Sleeper mean",
                         "both land scores on a 0.01 grid; DK offence lives on 0.02"],
           "consequence": ["the sole-win column is decided by tie frequency, and a grid twice as "
                           "fine as the real one makes exact ties about half as likely",
                           "dfs_tourney.SD_MONEY_LATTICE_OK is False, so the showdown money gate "
                           "stays shut for this reason independently of the field model"],
           "blocked_by": ["fixing it inside the legacy simulator would move every classic number "
                          "validated in the previous pass, which this pass may not do",
                          "the constrained simulator already scores on the exact lattice (0 of "
                          "1,600,000 off it) but promoting it is not this pass's decision"]}
    with open(os.path.join(DATA, "sd_lattice.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    log(f"[SDL] TOTAL {tot_bad:,} of {tot_n:,} ({100.0 * tot_bad / tot_n:.2f}%) off the DK lattice")
    return out


if __name__ == "__main__":
    fd = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA, "feeds")
    run(fd)
    print("showdown lattice audit written")
