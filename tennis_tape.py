"""In-play detection from Kalshi's own trade tape — the ITF live feed that does
not exist anywhere else.

THE GAP. ESPN publishes atp and wta scoreboards and nothing below them: itf,
itf-men, itf-women, atp-challenger and wta-challenger all 400/404. ITF is over
90% of a Kalshi tennis board, so more than nine matches in ten had no way to be
marked live — which is why an in-progress ITF match could sit in the Edges tab
posting a fabricated edge, and walk into a parlay, while never appearing in the
Live tab.

Everything obvious was checked first and none of it works:

    /events/<ticker>          no match state; product_metadata is just "ITF"
    occurrence_datetime       a scheduling bucket. Near-decided markets and
                              untouched ones have the SAME median, 5.2h out
    previous_yes_ask          unpopulated on 296 of 320 markets
    volume_24h                confounded by popularity, not state: a PRE match
                              (Rinderknech, 138k) outranks three LIVE ones

THE SIGNAL. A tennis match in play trades CONTINUOUSLY — every point moves the
price — and a scheduled one trades in occasional bursts. So the tell is not how
much a market has traded but how FAST, and Kalshi publishes the trade tape.

Measured against ESPN ground truth on a live ATP/WTA slate, taking the wall-clock
minutes spanned by a market's last 40 trades:

      LIVE   Tsitsipas/Fonseca      2.0     PRE   Zverev/Griekspoor    31.4
             Bergs/Baez             1.5           Shelton/Brooksby     34.9
             Vacherot/Navone        1.8           Rinderknech/Kecm.    42.1
             Etcheverry/Borges      3.2           Parks/Eala           77.8
             Cross/Li               4.2           Ruud/Cerundolo      101.1
             Musetti/Mejia          8.0           Linette/Jovic      1398.1

      LIVE   1.5 - 8.0 min          PRE   31.4 - 1398.1 min

Clean separation with a fourfold gap, and the threshold sits in the middle of it.
The feature is SCALE-FREE, which is the point: it asks how fast a market is
trading rather than how much, so a thin ITF book and a heavy ATP one are read the
same way. That is what lets a signal calibrated on ATP/WTA be trusted on ITF,
where there is no scoreboard to check it against.
"""

import concurrent.futures
import datetime

import kalshi
import racing

_SERIES = ("KXATPMATCH", "KXWTAMATCH", "KXITFMATCH", "KXITFWMATCH")

# Trades sampled per market. Enough that a burst of pre-match activity cannot
# masquerade as a rally, few enough to stay one cheap request.
_SAMPLE = 40
# Minutes the sample may span and still be called in-play. Sits in the middle of
# the measured gap (8.0 live, 31.4 pre) rather than hard against either edge.
_LIVE_SPAN_MIN = 15.0
# Below this many trades there is no velocity to measure, so the market is not
# claimed either way -- silence is not evidence of a match being scheduled.
_MIN_TRADES = 12
_TTL = 60                      # matches the ESPN snapshot's cadence
_WORKERS = 12


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _bulk_volume():
    """{ticker: volume_24h} for every open tennis market, in four requests.

    The pre-filter. A market with no trading at all today cannot be in play, and
    skipping those keeps the tape check to the handful of markets where the
    answer is in doubt instead of every match on the board."""
    def build():
        out = {}
        for s in _SERIES:
            cursor = ""
            for _ in range(6):
                url = (f"{kalshi.BASE}/markets?series_ticker={s}"
                       f"&status=open&limit=1000")
                if cursor:
                    url += f"&cursor={cursor}"
                try:
                    d = kalshi._get_json(url, timeout=20)
                except Exception:
                    break
                for m in d.get("markets") or []:
                    try:
                        out[m["ticker"]] = float(m.get("volume_24h_fp") or 0)
                    except (TypeError, ValueError, KeyError):
                        continue
                cursor = d.get("cursor") or ""
                if not cursor:
                    break
        return out
    return racing._cached(("tennis_tape_vol",), _TTL, build) or {}


def _span_minutes(ticker):
    """Wall-clock minutes spanned by this market's last `_SAMPLE` trades, or None
    when there are too few to say anything."""
    try:
        d = kalshi._get_json(
            f"{kalshi.BASE}/markets/trades?ticker={ticker}&limit={_SAMPLE}",
            timeout=12)
    except Exception:
        return None
    stamps = []
    for t in d.get("trades") or []:
        s = t.get("created_time")
        if not s:
            continue
        try:
            stamps.append(datetime.datetime.fromisoformat(s.replace("Z", "+00:00")))
        except ValueError:
            continue
    if len(stamps) < _MIN_TRADES:
        return None
    return (max(stamps) - min(stamps)).total_seconds() / 60.0


def in_play(matches):
    """Event tickers among `matches` whose trade tape says they are on court.

    Cached for the same 60s the ESPN snapshot uses. attach() runs per REQUEST,
    and the uncached pass costs ~9s against a full board -- fine once a minute,
    absurd on every page load."""
    return racing._cached(("tennis_tape_live",), _TTL, lambda: _in_play(matches)) or set()


def _in_play(matches):
    """Only markets that have traded today are checked, and the check runs
    concurrently -- a board is ~160 matches and doing this one at a time would
    cost more than the board it is annotating."""
    vol = _bulk_volume()
    todo = []
    for m in matches or []:
        ev = m.get("event")
        if not ev:
            continue
        # One side is enough: both halves of a match trade together, so the
        # busier book answers the question at half the cost.
        cands = [(m.get(s) or {}).get("ticker") for s in ("a", "b")]
        cands = [t for t in cands if t and vol.get(t, 0) > 0]
        if not cands:
            continue
        todo.append((ev, max(cands, key=lambda t: vol.get(t, 0))))
    if not todo:
        return set()

    def check(pair):
        ev, tk = pair
        span = _span_minutes(tk)
        return ev, (span is not None and span <= _LIVE_SPAN_MIN)

    live = set()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=_WORKERS) as ex:
            for ev, hot in ex.map(check, todo):
                if hot:
                    live.add(ev)
    except Exception:
        return live
    return live


def attach(board):
    """Mark tape-detected in-play matches on a board, without disturbing the ones
    ESPN already covers.

    ESPN wins where it has an opinion: it carries the SCORE, and the upset radar
    re-simulates from that. The tape only ever adds matches ESPN cannot see --
    which is essentially all of ITF."""
    if not board:
        return board
    matches = board.get("matches") or []
    unknown = [m for m in matches if not m.get("live")]
    if not unknown:
        return board
    try:
        hot = in_play(unknown)
    except Exception:
        return board
    if not hot:
        return board
    out = []
    for m in matches:
        if not m.get("live") and m.get("event") in hot:
            m = dict(m)
            # No score: the tape says a match is being played, not what the
            # score is. Saying "in play" and stopping there is the honest shape
            # -- anything more would be invented.
            m["live"] = {"detail": "in play", "source": "tape", "score": None}
            m["live_source"] = "tape"
        out.append(m)
    b = dict(board)
    b["matches"] = out
    b["n_live"] = sum(1 for m in out if m.get("live"))
    return b
