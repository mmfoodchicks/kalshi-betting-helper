"""THE registered R5 read on the 2027 regular season: the owner-approved amendment
(research/data/nfl_coach/prereg_r5_2027.json; the owner, 2026-11-27: "Allow R5 to
use the 2027 regular season rather than waiting until January 2029. Freeze its
implementation and acceptance criteria now."). Read ONCE, on or after 2028-01-13
06:00 UTC, after R1's and R4's reads, from a clean tree at a commit holding every
pinned file:

  python3 -m research.nfl_coach_r5_read_2027 <dir: fresh play_by_play_2027.csv.gz, players.csv,
                                               injuries_2027.csv>

This file is a thin wrapper: the statistics, the criteria and the refusals are
nfl_coach_r5_read's, frozen by prereg_r5.json, run against the amendment's block
(its window, its pins) and writing read_r5_2027.json. prereg_r5.json (the 2028
read) is not changed by this and stays a later replication on games no 2027 read
sees. Refuses additionally when prereg_r5.json no longer matches the hash the
amendment recorded.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from research import nfl_coach_r5_read as RR  # noqa: E402

PREREG = os.path.join(ROOT, "research", "data", "nfl_coach", "prereg_r5_2027.json")
OUT = os.path.join(ROOT, "research", "data", "nfl_coach", "read_r5_2027.json")
ORIGINAL = "research/data/nfl_coach/prereg_r5.json"


def prereg():
    with open(PREREG) as fh:
        return json.load(fh)


class _AsAmended:
    """nfl_coach_r5_read's functions pointed at the amendment's block and output."""

    def __enter__(self):
        self.saved = (RR.PREREG, RR.OUT)
        RR.PREREG, RR.OUT = PREREG, OUT
        return RR

    def __exit__(self, *a):
        RR.PREREG, RR.OUT = self.saved
        return False


def refusal(now_utc=None, dirty=None, reg=None, earlier_read=None, already=None):
    reg = reg or prereg()
    if RR._sha(ORIGINAL) != reg["amends"]["file_sha256"]:
        return "the original registration (prereg_r5.json) differs from the hash this amendment recorded"
    with _AsAmended() as rr:
        return rr.refusal(now_utc=now_utc, dirty=dirty, reg=reg, earlier_read=earlier_read, already=already)


def verdict(s):
    return RR.verdict(s)


def main(argv):
    why = refusal() if argv and not argv[0].startswith("-") else None
    if why:
        print(f"REFUSED: {why}")
        return 1
    with _AsAmended() as rr:
        return rr.main(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
