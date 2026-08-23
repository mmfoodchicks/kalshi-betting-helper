"""NFL model track record — the MLB pick ledger, ported.

Records the drive engine's pick for every board game that is still PRE-GAME
and has a Kalshi moneyline: the entry price and probability freeze at first
sight (INSERT OR IGNORE), the closing price refreshes on later pre-game board
builds — same side only — and stops the moment the game kicks off. Winners
grade off ESPN's scoreboard once final. store.nfl_record() then reports the
same honest scoreboard MLB has: W-L, taker-fee ROI, edge-filtered ROI, CLV,
Brier, calibration — with preseason in its OWN bucket, because exhibitions
and the regular season are different distributions (see nfl_game_sim's
predlog split for the measured gap).
"""

import datetime

import errlog
import kalshi
import store

# ESPN and the board (Sleeper/Kalshi) disagree on a few abbreviations; both
# sides are canonicalized before comparing so a Washington final actually
# grades a Washington pick.
_ALIAS = {"WSH": "WAS", "JAC": "JAX", "OAK": "LV", "SD": "LAC", "STL": "LAR"}

_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"


def canon(ab):
    ab = (ab or "").upper()
    return _ALIAS.get(ab, ab)


def record_from_board(data):
    """Log every pre-game board game that has a Kalshi price for the model's
    pick; refresh same-side closes. Returns how many rows were touched."""
    if not data or data.get("empty"):
        return 0
    week = data.get("week")
    pre = bool(data.get("preseason"))
    n = 0
    for g in data.get("games") or []:
        state = (g.get("state") or "").lower()
        if state and state != "pre":
            continue                  # live/final: never write prices again
        ph = g.get("p_home")
        kx = g.get("kalshi") or {}
        if ph is None:
            continue
        pick_home = ph >= 0.5
        price = kx.get("home_cents" if pick_home else "away_cents")
        if price is None:
            continue                  # no market for our side -> nothing to grade ROI on
        date = (g.get("date") or "")[:10]
        home, away = canon(g.get("home")), canon(g.get("away"))
        if not (date and home and away):
            continue
        gid = f"{date}_{away}@{home}"
        side = "home" if pick_home else "away"
        raw = g.get("p_home_raw")
        store.record_nfl_pick(
            gid, date, week, pre, side, home if pick_home else away,
            ph if pick_home else 1 - ph, price,
            pred_total=g.get("exp_total"),
            prob_raw=(raw if pick_home else 1 - raw) if raw is not None else None)
        store.update_nfl_close(gid, price, side)
        n += 1
    return n


def _finals_for(date):
    """{(home, away): (home_score, away_score)} for games FINAL on `date`
    (YYYY-MM-DD), from ESPN's scoreboard. Abbreviations canonicalized."""
    d = kalshi._get_json(f"{_SCOREBOARD}?dates={date.replace('-', '')}")
    out = {}
    for ev in d.get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        if (((comp.get("status") or {}).get("type")) or {}).get("state") != "post":
            continue
        home = away = None
        hs = as_ = None
        for c in comp.get("competitors") or []:
            ab = canon(((c.get("team") or {}).get("abbreviation")))
            try:
                sc = float(c.get("score"))
            except (TypeError, ValueError):
                sc = None
            if c.get("homeAway") == "home":
                home, hs = ab, sc
            else:
                away, as_ = ab, sc
        if home and away and hs is not None and as_ is not None:
            out[(home, away)] = (hs, as_)
    return out


def _grade_rows(picks, finals):
    """Pure grading: [(game_id, won, winner_name, actual_total, home_won)] for
    every pick whose game appears in `finals`. A TIE grades as a loss for
    either side — that is how a Kalshi winner market settles it — with
    winner_name 'TIE' and no home_won (it feeds no split)."""
    out = []
    for p in picks:
        gid = p["game_id"]
        try:
            _date, matchup = gid.split("_", 1)
            away, home = matchup.split("@", 1)
        except ValueError:
            continue
        res = finals.get((canon(home), canon(away)))
        if res is None:
            continue
        hs, as_ = res
        total = hs + as_
        if hs == as_:
            out.append((gid, 0, "TIE", total, None))
            continue
        home_won = 1 if hs > as_ else 0
        won = home_won if p.get("pick_side") == "home" else 1 - home_won
        winner = home if home_won else away
        out.append((gid, won, winner, total, home_won))
    return out


def grade_due():
    """Grade any recorded picks whose games are now final. Same ±1-day sweep
    as baseball.grade_picks — late kickoffs cross the calendar date."""
    picks = store.ungraded_nfl_picks()
    if not picks:
        return 0
    by_date = {}
    for p in picks:
        by_date.setdefault(p["date"], []).append(p)
    n = 0
    for date, ps in by_date.items():
        finals = {}
        try:
            d0 = datetime.date.fromisoformat(date)
            for off in (0, 1, -1):
                finals.update(_finals_for((d0 + datetime.timedelta(days=off)).isoformat()))
        except Exception as _e:
            errlog.note("NFLT-finals", _e)
            continue
        for gid, won, winner, total, home_won in _grade_rows(ps, finals):
            store.set_nfl_grade(gid, won, winner, actual_total=total, home_won=home_won)
            n += 1
    return n
