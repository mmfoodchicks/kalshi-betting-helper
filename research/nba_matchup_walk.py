"""The chronological walk the matchup fit reads (RESEARCH ONLY): every
player-game of 2021-22 .. 2025-26 with its as-of profiles, built by the SAME
nba_matchup_profiles.Book production uses.

Each date's rows are snapshotted BEFORE that date's games enter the book
(the as-of rule: strictly earlier dates; a doubleheader day cannot see
itself). The game day is the Mountain-time date of the tip
(UTC minus six hours; the owner's clock and clock.mt_date's).
"""
import math
import os
import pickle
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import nba_matchup as NM              # noqa: E402
import nba_matchup_profiles as M      # noqa: E402


# ONE game-day function for research and production (reviews 2026-10-23,
# F10: two copies could drift into a same-day leak); guarded.
game_day = NM.game_day


def load_entries(store_dir, seasons):
    out = []
    for s in seasons:
        p = os.path.join(store_dir, f"nba_matchup_{s}.pkl")
        with open(p, "rb") as fh:
            st = pickle.load(fh)["payload"]
        for gid, e in st["games"].items():
            if e.get("st") not in (2, None):
                continue
            e = dict(e)
            e["day"] = game_day(e["date"])
            e["season"] = s
            out.append(e)
    out.sort(key=lambda e: (e["day"], e["id"]))
    return out


def by_day(entries):
    cur, day = [], None
    for e in entries:
        if e["day"] != day and cur:
            yield day, cur
            cur = []
        day = e["day"]
        cur.append(e)
    if cur:
        yield day, cur


def walk(entries, on_date, expected_fn=None, log=print):
    """Feed the book date by date. `on_date(book, day, games)` is called
    BEFORE the date's games are added and returns whatever the caller
    collects; `expected_fn(book_snapshots)` may return the {gid: {pid: {stat:
    E}}} for the observed-over-expected sums."""
    book = M.Book()
    n = 0
    for day, games in by_day(entries):
        book.roll(games[0]["season"])
        ex = on_date(book, day, games)
        book.add_date([dict(g, date=day) for g in games], expected=ex)
        n += 1
        if log and n % 200 == 0:
            log(f"[walk] {day}: {n} dates")
    return book


def safe_log(x, floor=1e-6):
    return math.log(max(floor, x))
