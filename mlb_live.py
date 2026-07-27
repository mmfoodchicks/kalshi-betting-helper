"""Mid-game state for a live MLB game, in the shape the simulator can resume from.

The pre-game sim answers "how will this game go?". Once first pitch has been
thrown that's the wrong question: Schwarber's 1+ hits prop doesn't care what he
was projected to do, it cares whether he's already got one -- and if he has, the
market is settled at 100%. What's left to model is the REST of the game, played
out from exactly where it stands, with everything already banked added on top.

This module reads that "exactly where it stands" out of MLB's live feed:

  * the base-out state and score -- which inning, which half, how many down, who
    is standing on which base
  * each side's next spot in the batting order, so the remainder resumes with the
    right man at the plate
  * what every batter has already banked (H / TB / HR / R / RBI / SB), so a prop
    becomes "banked + rest of game" -- which is what Kalshi settles on
  * each staff's pitching state -- the starter's Ks, pitches and outs, whether
    he's even still in the game, and the bullpen's Ks so far

Everything is read from the boxscore rather than replayed from the play log, so
a snapshot costs one request and stays correct through substitutions.
"""

import racing

_FEED = "https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live"

# How long a snapshot is worth reusing. A live game moves fast and the whole
# point is to price off the current state, so this is deliberately short.
_TTL = 20


def _bat(p):
    return ((p.get("stats") or {}).get("batting") or {})


def _pit(p):
    return ((p.get("stats") or {}).get("pitching") or {})


def _order_idx(team_box):
    """0-based lineup spot of the man due up.

    Derived from total plate appearances mod 9 rather than the linescore's
    `battingOrder`: a PA is only counted once it COMPLETES, so the running total
    points at whoever is up now, and it stays right through the Middle/End states
    where the feed has already flipped offense and defense to the next half.
    """
    pa = sum(_bat(p).get("plateAppearances", 0) or 0
             for p in (team_box.get("players") or {}).values())
    return pa % 9


def _lineup(team_box):
    """[(player_id, name)] in batting order, starters only (9 spots)."""
    out = []
    for pid in (team_box.get("battingOrder") or [])[:9]:
        p = (team_box.get("players") or {}).get(f"ID{pid}") or {}
        nm = (p.get("person") or {}).get("fullName")
        if nm:
            out.append((pid, nm))
    return out


def _banked(team_box):
    """{player name: {hit, tb, hr, r, rbi, sb, k}} already in the book.

    Keyed by name because that is what the simulator's per-batter arrays use.
    Every player is included, not just the nine who started: when a sub has
    already banked a hit it still counts toward that market settling.
    """
    out = {}
    for p in (team_box.get("players") or {}).values():
        b = _bat(p)
        if not b:
            continue
        nm = (p.get("person") or {}).get("fullName")
        if not nm:
            continue
        out[nm] = {"hit": b.get("hits", 0) or 0, "tb": b.get("totalBases", 0) or 0,
                   "hr": b.get("homeRuns", 0) or 0, "r": b.get("runs", 0) or 0,
                   "rbi": b.get("rbi", 0) or 0, "sb": b.get("stolenBases", 0) or 0,
                   "k": b.get("strikeOuts", 0) or 0}
    return out


def _pitching(team_box):
    """This staff's state: the starter's line, whether he's still out there, the
    bullpen's Ks, and how many outs the staff has recorded."""
    players = team_box.get("players") or {}
    order = team_box.get("pitchers") or []
    starter_id = None
    for pid in order:
        if _pit(players.get(f"ID{pid}") or {}).get("gamesStarted"):
            starter_id = pid
            break
    if starter_id is None and order:
        starter_id = order[0]
    sp = _pit(players.get(f"ID{starter_id}") or {}) if starter_id else {}
    team = (team_box.get("teamStats") or {}).get("pitching") or {}
    team_k = team.get("strikeOuts", 0) or 0
    sp_k = sp.get("strikeOuts", 0) or 0
    return {
        "sp_id": starter_id,
        "sp_name": ((players.get(f"ID{starter_id}") or {}).get("person") or {}).get("fullName"),
        "sp_k": sp_k,
        "sp_outs": sp.get("outs", 0) or 0,
        "sp_pitches": sp.get("numberOfPitches", 0) or 0,
        # Traffic he has already allowed -- the engine's hook logic reads this to
        # decide whether he has earned a longer leash or is getting pulled early.
        "sp_br": (sp.get("hits", 0) or 0) + (sp.get("baseOnBalls", 0) or 0),
        # The starter is still in only if no one has relieved him yet.
        "sp_in": bool(order) and order[-1] == starter_id,
        "bull_k": max(0, team_k - sp_k),
        "outs": team.get("outs", 0) or 0,       # outs this staff has recorded
    }


# The half a game resumes in. "Middle" means the top is over and the bottom
# hasn't started; "End" means the whole inning is done.
def _resume_point(ls):
    """(inning, is_top, outs, fresh) -- where play picks up. `fresh` marks a
    clean half-inning start, where the base-out state must be ignored: the feed
    still reports the previous half's runners during Middle/End."""
    inning = ls.get("currentInning") or 1
    state = (ls.get("inningState") or "").strip().lower()
    if state == "middle":
        return inning, False, 0, True          # top done -> bottom of the same inning
    if state == "end":
        return inning + 1, True, 0, True       # inning done -> top of the next
    return inning, bool(ls.get("isTopInning")), int(ls.get("outs") or 0), False


def snapshot(game_pk, timecode=None):
    """The full mid-game state, or None if the feed can't be read.

    `timecode` (YYYYmmdd_HHMMSS) replays the game as of an earlier moment, which
    is how this is tested against finished games.
    """
    def build():
        url = _FEED.format(pk=game_pk)
        if timecode:
            url += f"?timecode={timecode}"
        try:
            feed = racing._get_json(url, timeout=25)
        except Exception:
            return None
        ld = feed.get("liveData") or {}
        ls = ld.get("linescore") or {}
        box = (ld.get("boxscore") or {}).get("teams") or {}
        if not box.get("home") or not box.get("away"):
            return None
        inning, is_top, outs, fresh = _resume_point(ls)
        teams = ls.get("teams") or {}
        off = ls.get("offense") or {}
        # Runners are only meaningful mid-half; at a Middle/End boundary the feed
        # still shows the half that just ended, and those runners are stranded.
        bases = [None, None, None]
        if not fresh:
            for i, key in enumerate(("first", "second", "third")):
                who = off.get(key) or {}
                if who.get("fullName"):
                    bases[i] = who["fullName"]
        gd = feed.get("gameData") or {}
        status = (gd.get("status") or {}).get("detailedState") or ""
        # Runs in the 1st. Once that inning is behind us the RFI market is
        # decided, so the sim must read the result rather than re-roll it.
        innings = ls.get("innings") or []
        first = innings[0] if innings else {}
        rfi_runs = (((first.get("away") or {}).get("runs") or 0)
                    + ((first.get("home") or {}).get("runs") or 0))
        return {
            "game_pk": game_pk,
            "status": status,
            "inning": inning,
            "is_top": is_top,
            "outs": outs,
            "bases": bases,                         # names on 1st/2nd/3rd, or None
            "away_runs": (teams.get("away") or {}).get("runs") or 0,
            "home_runs": (teams.get("home") or {}).get("runs") or 0,
            # The 1st is over once we're past it, or past its bottom half.
            "rfi_settled": bool(inning > 1 or (inning == 1 and not is_top)),
            "rfi_runs": rfi_runs,
            "order_idx": {"away": _order_idx(box["away"]),
                          "home": _order_idx(box["home"])},
            "lineup": {"away": _lineup(box["away"]), "home": _lineup(box["home"])},
            "banked": {"away": _banked(box["away"]), "home": _banked(box["home"])},
            # Keyed by the team that is PITCHING.
            "pitching": {"away": _pitching(box["away"]), "home": _pitching(box["home"])},
        }
    if timecode:
        return build()                              # test replay: never cached
    return racing._cached(("mlb_live_snap", game_pk), _TTL, build)


def describe(snap):
    """One-line human summary, e.g. 'Top 3rd, 2 out, NYY 0 - PHI 2'."""
    if not snap:
        return ""
    half = "Top" if snap["is_top"] else "Bot"
    o = snap["outs"]
    return (f"{half} {snap['inning']}, {o} out{'' if o == 1 else 's'}, "
            f"{snap['away_runs']}-{snap['home_runs']}")
