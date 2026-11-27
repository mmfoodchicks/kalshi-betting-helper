"""C3-N: the NFL season sim's analytic resolution at its OWN points-per-win, on fresh games. REGISTERED 2026-11-27.

Why this exists. C3 (research/c3_season_replay.py, read 2026-11-27, research/data/c3_season_replay.json -- kept as
read) compared the drive engine with Phi(margin / 13.2) on "the numbers the boards already use": the margin
(Ra - Rb) x nfl_season._PTS_PER_WIN, 2.25. That constant was fitted so the COMPRESSED drive engine gives the
market's wins back (1.66 / 0.74). research/c3_margin_scale.py measured, by the constant's own criterion (seed 32
teams with a market-like win vector, play 2025's schedule, regress returned wins on seeded wins), that the drive
engine at 2.25 returns slope 1.04 +/- 0.025 and Phi(. / 13.2) at 2.25 returns 1.19 +/- 0.03: C3's analytic arm
over-spread the ratings by about a fifth. The slope-1 points-per-win for Phi(. / 13.2) is K_NEW = 1.842 (exact
expectations, bisection). C3 answered its own registered question and stays read; THIS asks the question it could
not: does the analytic resolution, scaled to give the market's wins back, beat production's drive engine?

THE RULE (written, hashed into research/data/c3_nfl_rescaled/prereg.json and committed before any game it scores
was played; never edited -- prereg.json pins this file and the machinery it imports by sha256):

  Data. FRESH games only. 2026: the projections before weeks 13-18 (every game they simulate is played on or
    after 2026-12-03, after this registration); 2027: the projections before weeks 1-18. ESPN scoreboards and the
    standings' playoffSeed (research/c3_season_replay.fetch_nfl), fetched at the read.
  Arms. Both on C3's machinery unchanged (c3_season_replay.nfl_asof, sim_nfl; settlements S2-S4, S7, S8): the
    record-only as-of rating (production's market-anchored rating cannot be replayed afterwards -- the ladder's
    history is not kept), production's schedule handling, n = 4,000 seasons a week, seed crc32("c3n|season|week")
    shared by both arms.
      engine    production's drive engine at nfl_season._PTS_PER_WIN as the read finds it (the decision is about
                production then; the read records the commit);
      rescaled  Phi(((Ra - Rb) x 1.842 + 2.2 x host) / 13.2), regular season and playoffs alike.
  Scores. Per team per week: |E[wins] - real wins| and the division + playoffs log loss (C3's S7/S8), d = engine -
    rescaled (positive: rescaled better), pooled over both windows, SE clustered by TEAM-SEASON (64 clusters).
  Verdict. PASS iff the pooled d exceeds 2 SE on BOTH scores. Anything else: NOT ESTABLISHED, with the 95%
    bound; production unchanged. Reported beside it, deciding nothing: each season alone, and rescaled vs C3's
    2.25 analytic arm.
  Power, stated now. C3 measured a per-season SE of 0.013-0.014 wins (MAE) and 0.011 (log loss) per team-week
    over 18 weekly projections; the late-2026 window adds little (few games left, small errors). Pooled SE about
    0.012 / 0.010, so 80% power needs a true gain of about 0.034 wins and 0.028 nats per team-week. C3's point
    estimates for the over-spread arm were +0.006 to +0.016 (MAE) and -0.012 to -0.027 (log loss): unless the
    rescaled arm is much better than that, expect NOT ESTABLISHED. A null is never "no difference".
  Once. Not before 2028-01-12T00:00:00Z (2027's week 18 played, the seeds final); refuses a dirty tree, a hash
  that does not match prereg.json, and an existing result.

usage: VIGIL_NO_BG=1 python3 research/c3_nfl_rescaled.py read --cache <dir>
"""
import datetime
import hashlib
import json
import os
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("VIGIL_NO_BG", "1")

import nfl_season                                   # noqa: E402
import research.c3_season_replay as C               # noqa: E402

K_NEW = 1.842
WINDOWS = {2026: tuple(range(13, 19)), 2027: tuple(range(1, 19))}
ARMS = ("engine", "rescaled")
N = 4000
DIR = os.path.join(ROOT, "research", "data", "c3_nfl_rescaled")
PREREG = os.path.join(DIR, "prereg.json")
RESULT = os.path.join(DIR, "read.json")
PINNED = ("research/c3_nfl_rescaled.py", "research/c3_season_replay.py")
NOT_BEFORE = "2028-01-12T00:00:00Z"


def sha(rel):
    return hashlib.sha256(open(os.path.join(ROOT, rel), "rb").read()).hexdigest()


def rescaled_prep(ra, rb, edge):
    return C._phi(((ra - rb) * K_NEW + nfl_season._HFA_MARGIN * edge) / C.SIGMA["nfl"])


def sim(A, arm, n, seed):
    """C3's sim_nfl; the rescaled arm is its analytic arm with the margin at K_NEW (prep swapped for the call)."""
    if arm == "engine":
        return C.sim_nfl(A, "engine", n, seed)
    orig = C.nfl_prep
    try:
        C.nfl_prep = lambda a_, ra, rb, edge: rescaled_prep(ra, rb, edge)
        return C.sim_nfl(A, "analytic", n, seed)
    finally:
        C.nfl_prep = orig


def seed_for(season, week):
    return zlib.crc32(f"c3n|{season}|{week}".encode())


def score(per_season, base="engine", alt="rescaled"):
    """{season: ({week: {arm: sim result}}, real outcomes)} -> d = base - alt pooled, clustered by team-season."""
    d_mae, d_ll, cl = [], [], []
    for season, (weeks, real) in per_season.items():
        for w, P in weeks.items():
            for t in sorted(real):
                got = {arm: C.terms("nfl", real[t], P[arm]["counts"][t], P[arm]["n"]) for arm in (base, alt)}
                d_mae.append(got[base][0] - got[alt][0])
                d_ll.append(got[base][1] - got[alt][1])
                cl.append(f"{season}:{t}")
    mae, ll = C.paired(d_mae, cl), C.paired(d_ll, cl)
    ok = C.wins_by_2se(mae) and C.wins_by_2se(ll)
    return {"mae": mae, "ll": ll, "verdict": "PASS" if ok else "NOT ESTABLISHED"}


def read(cache):
    now = datetime.datetime.now(datetime.timezone.utc)
    if now < datetime.datetime.strptime(NOT_BEFORE, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc):
        sys.exit(f"sealed until {NOT_BEFORE}")
    if os.path.exists(RESULT):
        sys.exit("refusing: read once")
    reg = json.load(open(PREREG))
    bad = [p for p in PINNED if reg["sha256"].get(p) != sha(p)]
    if bad:
        sys.exit(f"refusing: {bad} differ from the registration")
    head = C._clean_or_die()
    get = C._install_cache(cache)
    per = {}
    for season, weeks in WINDOWS.items():
        ni = C.fetch_nfl(get, season)
        real = C.real_nfl(ni)
        P = {}
        for w in weeks:
            A = C.nfl_asof(ni, w)
            P[w] = {arm: sim(A, arm, N, seed_for(season, w)) for arm in ARMS}
            P[w]["analytic_225"] = C.sim_nfl(A, "analytic", N, seed_for(season, w))    # C3's arm, reported only
        per[season] = (P, real)
    res = {"rule": "research/data/c3_nfl_rescaled/prereg.json", "commit": head, "k_new": K_NEW,
           "pts_per_win_engine": nfl_season._PTS_PER_WIN, "pooled": score(per),
           "by_season": {s: score({s: per[s]}) for s in per},
           "rescaled_vs_c3_arm": score(per, base="analytic_225", alt="rescaled")}
    with open(RESULT, "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
    print(json.dumps(res["pooled"], indent=1))


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "read" and sys.argv[2] == "--cache":
        read(sys.argv[3])
    else:
        sys.exit(__doc__)
