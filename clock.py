"""The app's clock. US sports (and Kalshi's trading day) live on Eastern time,
so "today's slate", nightly-rerun boundaries and season years must be computed
in ET — a server on UTC otherwise flips to "tomorrow" at 8 pm ET, mid-slate:
the featured tab would show the wrong day's games every night, and the
"midnight" reruns would fire while the evening games are still being played.
"""
import datetime as _dt
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def now_et():
    return _dt.datetime.now(ET)


def today_et():
    return now_et().date()


def midnight_et_epoch():
    """Epoch of the most recent ET midnight (the nightly-job boundary)."""
    return now_et().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


# The owner reads every time in Mountain time (CLAUDE.md), so "today" on a
# slip the owner places is their calendar day, not the exchange's: an 8 pm
# MT kickoff is tomorrow in ET and UTC and still tonight to the owner.
MT = ZoneInfo("America/Denver")


def mt_date(ts):
    """The Mountain-time calendar date of an epoch."""
    return _dt.datetime.fromtimestamp(ts, MT).date()
