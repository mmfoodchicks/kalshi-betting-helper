"""DraftKings NFL Classic DFS: optimizer + correlated contest sim.

Roster: 1 QB, 2 RB, 3 WR, 1 TE, 1 FLEX (RB/WR/TE), 1 DST -- $50,000 cap.

Projections + per-player score arrays come from the Sleeper-seeded correlated
game sim (nfl_dfs_sim.player_pool): skill players carry the within-game
correlation (a QB and his WRs boom together), DSTs are sampled independently.
Salaries + the actual player pool come from DraftKings' public lobby (dk.py;
a pasted DKSalaries.csv still works). Ceiling /
leverage objectives and the top-heavy contest sim mirror the MLB DFS builder;
the pure payout-curve helpers are reused straight from mlb_dfs.
"""

import collections
import math
import random

import errlog
import simulate                     # parse_dk_csv
import nfl_dfs_sim
from mlb_dfs import _gpp_curve, _rank_grid, _ncdf, _npdf   # roster-agnostic helpers

ROSTER = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"]
FLEX_OK = ("RB", "WR")   # TE is DK-legal at FLEX and almost always wrong there:
                         # the slot swaps ~2 points of RB/WR scoring for a
                         # second tight end, and the niche elite-TE double
                         # build is not what an optimizer should default into
CAP = 50000

# --- Showdown Captain Mode ---------------------------------------------------
# A one-game slate has its own roster: 1 CPT + 5 FLEX, any position, same $50,000
# cap. The captain scores 1.5x and costs 1.5x, and DK's export lists every player
# TWICE -- once with Roster Position CPT at the higher salary, once as FLEX.
#
# Running classic rules against a showdown export is what produced "no valid
# lineup under the cap", and it could not have produced anything else: classic
# needs nine players, and on tonight's Panthers-Cardinals export DK flat-priced
# every FLEX at $7,600, so nine of them is $68,400 against a $50,000 cap. The
# showdown roster fits with room to spare -- 11,400 + 5 x 7,600 = $49,400.
SHOWDOWN_ROSTER = ["CPT", "FLEX", "FLEX", "FLEX", "FLEX", "FLEX"]
CPT_MULT = 1.5
# Kickers exist in showdown and not in classic, which is why _elig refuses them.
SHOWDOWN_POS = {"QB", "RB", "WR", "TE", "K", "DST"}
# DK requires a showdown roster to span both teams -- you cannot enter six
# players from one side.
SHOWDOWN_MIN_TEAMS = 2
# DK's own "this player is not playing" flags. Q (questionable) is deliberately
# NOT here: questionable players play all the time and pricing them out would
# throw away real leverage.
# DK's will-not-play statuses as the CSV's Status column carries them (dk.py
# writes them for an auto-loaded slate), plus Doubtful, which the depth-chart
# gate already treats as out on Sleeper's word (_INJ_OUT): with "D" missing a
# DK-doubtful player Sleeper had not marked survived the gate.
_OUT_STATUS = {"OUT", "O", "IR", "PUP", "NFI", "NA", "SUSP", "SUS", "D", "DOUBTFUL"}


def _elig(pos):
    pos = "RB" if pos == "FB" else (pos or "").upper()
    slots = [pos] if pos in ("QB", "RB", "WR", "TE", "DST") else []
    if pos in FLEX_OK:
        slots.append("FLEX")
    return slots


def _value(p, objective):
    if objective == "ceiling":
        v = p["ceiling"]
    elif objective == "leverage":
        # DST is exempt: the slot's score range is too small for contrarianism
        # to differentiate a lineup, so fading the chalk defense costs real
        # points to buy nothing -- leverage lives in the skill slots.
        if p.get("pos") == "DST":
            v = p["ceiling"]
        else:
            v = p["ceiling"] * (1.0 - 0.007 * p.get("own", 8.0))
    else:
        v = p["proj"]
    # a player the picker forced in rides a marked copy (never the field's dict)
    return v + _PK_BIAS if "_pk" in p else v


def _slate_dates(csv_players):
    """Game dates parsed off the CSV's own Game Info column (MM/DD/YYYY)."""
    import re as _re
    import datetime as _dt
    out = []
    for c in csv_players:
        m = _re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", c.get("game") or "")
        if m:
            try:
                out.append(_dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2))))
            except ValueError:
                pass
    return out


def _csv_kickoff(csv_players):
    """The earliest kickoff in a pasted export's Game Info ("NO@DET 09/13/2026
    01:00PM ET"), as DraftKings' own string shape for iso_ts, or None. The
    live feed's CSV carries no times, so this is None there and the caller
    passes the slate's own `starts`."""
    import re as _re
    best = None
    for c in csv_players:
        m = _re.search(r"\b(\d{2})/(\d{2})/(\d{4})\s+(\d{1,2}):(\d{2})\s*([AP]M)", c.get("game") or "", _re.I)
        if not m:
            continue
        hh = int(m.group(4)) % 12 + (12 if m.group(6).upper() == "PM" else 0)
        s = f"{m.group(3)}-{m.group(1)}-{m.group(2)}T{hh:02d}:{m.group(5)}:00"   # Eastern, no zone
        if best is None or s < best:
            best = s
    return best


def _lock_seconds(starts, csv_players, contest):
    """Seconds to lock for the depth gate's roster allowance: the slate's own
    kickoff (the live feed's `starts`, UTC), else the export's Game Info, else
    a contest dict's start. The sheet used to read only the last, and the
    route hands `contest` over as the STRING "gpp", so no sheet build was ever
    lock-relative."""
    for v in (starts, _csv_kickoff(csv_players or []),
              (contest.get("starts") if isinstance(contest, dict) else None)):
        s = seconds_to_lock(v) if v else None
        if s is not None:
            return s
    return None


def season_type_of_date(d):
    """(preseason, week) for a game date: NFL regular seasons start after
    Labor Day, so an August date is exhibition and a September-or-later one is
    real, the week counted from the first Thursday on or after Sep 4."""
    import datetime as _dt
    if d.month < 9 and not (d.month == 1 or d.month == 2):
        return True, None                       # August (or earlier): exhibition
    anchor = _dt.date(d.year if d.month >= 9 else d.year - 1, 9, 4)
    while anchor.weekday() != 3:                # first Thursday on/after Sep 4
        anchor += _dt.timedelta(days=1)
    return False, max(1, min(18, (d - anchor).days // 7 + 1))


def slate_season_type(csv_players, starts=None):
    """(preseason, week) read off the SLATE, or (None, None) when undatable.

    `starts` (the live feed's first kickoff) dates a slate whose CSV carries
    no dates: DraftKings' own export has them in Game Info, the live feed's
    CSV does not, and an auto-loaded week-6 slate used to fall through to the
    week picker's first option and build on WEEK 1 projections (measured
    2026-10-16 on dg 154949 and 154941: "Week 1 regular season (from the
    toggle - this CSV carries no game dates)").

    The old behavior asked the calendar -- "it is August, so this must be
    preseason" -- which ran DK's early-posted WEEK 1 slates through the
    inverted exhibition usage model: Jahmyr Gibbs projected to sit (7.6%
    owned) while a third-string TE drew 14%. The CSV says which games these
    are; the CSV decides. NFL regular seasons start after Labor Day, so an
    August slate is exhibition and a September-or-later one is real, with the
    week counted from the first Thursday on or after Sep 4."""
    import datetime as _dt
    dates = _slate_dates(csv_players)
    if not dates and starts:
        ts = iso_ts(starts)
        if ts is not None:
            import clock
            dates = [_dt.datetime.fromtimestamp(ts, clock.ET).date()]
    if not dates:
        return None, None
    return season_type_of_date(sorted(dates)[len(dates) // 2])


def _matchup(game):
    """("AWAY", "HOME") from a DraftKings game string in EITHER shape the app
    meets: the salary export's "NO@DET 09/13/2026 01:00PM ET" and the live
    feed's competition name "NO @ DET" (spaces round the @). None without an
    @. The old parser took the first space-separated token and looked for
    the @ inside it, so the feed's "NO" had none: every classic board built
    from the feed carried a blank opponent for every player (243 of 243 on
    2026-09-10), which silently switched off the per-game cap, the defense
    rule and the field's bring-backs while the export path kept passing its
    guard. The tokens either side of the @ are the teams in both shapes."""
    g = str(game or "").strip()
    if "@" not in g:
        return None
    left, right = g.split("@", 1)
    away = left.strip().split(" ")[-1].upper() if left.strip() else ""
    home = right.strip().split(" ")[0].upper() if right.strip() else ""
    if not away or not home or not away.isalnum() or not home.isalnum():
        return None
    return away, home


def game_key(game):
    """The canonical game identity, "AWAY@HOME", from either game-string
    shape; None when there is no matchup in it."""
    m = _matchup(game)
    return f"{m[0]}@{m[1]}" if m else None


def _opp_of(team, game):
    """The opponent abbr from a DK game string, either shape (see _matchup)."""
    m = _matchup(game)
    if not m:
        return None
    t = (team or "").upper()
    return m[1] if m[0] == t else (m[0] if m[1] == t else None)


def detect_mode(csv_players):
    """'showdown' or 'classic', read off the export itself.

    A showdown export is the one that carries CPT rows. Falling back to the game
    count is not enough on its own -- a one-game CLASSIC slate exists (a Monday
    nighter posted as a single-game classic contest) -- so the roster position is
    what decides, and a single game is only the tie-breaker when DK omitted it.
    """
    rps = {(c.get("roster_pos") or "").upper() for c in csv_players}
    if "CPT" in rps:
        return "showdown"
    games = {game_key(c.get("game")) or (c.get("game") or "").split(" ")[0] for c in csv_players if c.get("game")}
    if len(games) == 1 and rps and rps <= {"FLEX", "UTIL", ""}:
        return "showdown"
    return "classic"


def _playable(c):
    """Drop players DK has already ruled out. Rostering an OUT or IR player is
    never right, and the export says so plainly."""
    return (c.get("status") or "").upper() not in _OUT_STATUS


# ---- The player picker (2026-10-30) -----------------------------------------
# The owner: '"exclude player": (player name) if I want to exclude him from a
# build "force captain": (player) If I want to force that player to be
# captain ... force players into a position. Like if I wanted a team with
# dak, pickens, and ceedee lamb. It would create all lineups with those
# players forced into the lineup. Of course the automatic builds would do all
# available players anywhere.' Designed with a Fable agent before any code
# (research/reports/dfs_picker.md). A pick binds OUR lineups only: the FIELD
# still plays the man excluded, and a man forced past the depth gate never
# joins it. Only the owner's builds take one -- the sheet's Run and a Build
# queued for the PC; nothing on a schedule reads it.
_PK_BIAS = 1000.0       # points, more than any lineup scores: polish and kicks never swap a forced
                        # man out. Inside the search only; never in a reported number.
_PK_MAX = 30            # names one pick may list
_PICK_SHAPE = {"QB": 1, "RB": 3, "WR": 4, "TE": 1, "DST": 1}   # _lineup_ok and the boards' solver alike
Pick = collections.namedtuple("Pick", "exclude force captain")  # raw DK names; force holds the captain


def _pk_entry(x):
    if isinstance(x, (list, tuple)) and len(x) == 3:       # already parsed
        x = {"name": x[0], "team": x[1], "id": x[2]}
    if isinstance(x, str):
        x = {"name": x}
    if not isinstance(x, dict) or not str(x.get("name") or "").strip():
        raise ValueError("every picked player needs a name")
    return (str(x["name"]).strip()[:80], str(x.get("team") or "").strip().upper()[:5],
            str(x.get("id") or "").strip()[:20])


def pick_parse(raw):
    """The request's `pick` -- {exclude: [{name, team, id}], force: [...],
    captain: {...} or null} -- as (name, TEAM, id) triples, or None when it
    names nobody. ValueError on a malformed one. Always its own key: the
    sheet's body already carries `exclude` for GAMES (dk.slate_for)."""
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise ValueError("pick must be an object: {exclude, force, captain}")
    ex, fo = raw.get("exclude") or [], raw.get("force") or []
    if not isinstance(ex, list) or not isinstance(fo, list):
        raise ValueError("pick.exclude and pick.force must be lists")
    cap = raw.get("captain") or None
    out = {"exclude": [_pk_entry(x) for x in ex], "force": [_pk_entry(x) for x in fo],
           "captain": _pk_entry(cap) if cap else None}
    if len(out["exclude"]) + len(out["force"]) + (1 if out["captain"] else 0) > _PK_MAX:
        raise ValueError(f"a pick names at most {_PK_MAX} players")
    if not (out["exclude"] or out["force"] or out["captain"]):
        return None
    return out


def _pk_find(entry, rows, norm):
    """(raw DK name, None) of the one man `entry` names on this slate, or
    (None, why). Identity is the normalised name and the team; the id breaks
    a tie only. The auto-loaded CSV writes DraftKings' id of the MAN
    (playerDkId) on both of a showdown player's rows, but DraftKings' own
    export writes a per-SLOT id, different on his CPT and FLEX rows, and an
    old paste may have none -- so an id cannot be the key."""
    name, team, pid = entry

    def on_team(r):
        return not team or (r.get("team") or "").strip().upper() == team
    # DraftKings' own spelling first (the tab sends the pool's names); the
    # normalised one -- accents, suffixes and punctuation dropped -- only when
    # that finds no one, since it also drops digits and initials
    flat = " ".join(name.lower().split())
    hits = {}
    for r in rows:
        if on_team(r) and " ".join(str(r["name"]).lower().split()) == flat:
            hits.setdefault(r["name"], []).append(r)
    if not hits:
        nk = norm(name)
        for r in rows:
            if on_team(r) and norm(r["name"]) == nk:
                hits.setdefault(r["name"], []).append(r)
    if len(hits) > 1 and pid:
        hits = {k: v for k, v in hits.items() if any(str(r.get("dk_id") or "") == pid for r in v)}
    if not hits:
        return None, f"{name}{f' ({team})' if team else ''} is not on this slate"
    if len(hits) > 1:
        return None, f"two players on this slate match {name}; pick him from the list"
    return next(iter(hits)), None


def _cheapest_fill(slots, cands):
    """The cheapest salary that fills `slots` from `cands` [(name, salary,
    eligible slots)], each man once, or None: positions are separable, so
    the cheapest eligible man slot by slot (FLEX last) is the minimum."""
    used, tot = set(), 0
    for s in sorted(slots, key=lambda s: s == "FLEX"):
        best = min((c for c in cands if c[0] not in used and s in c[2]), key=lambda c: c[1], default=None)
        if best is None:
            return None
        used.add(best[0])
        tot += best[1]
    return tot


def pick_resolve(pp, rows, mode, cap=CAP, stack=True, board=False):
    """(Pick, None) or (None, why): a parsed pick resolved to this slate's raw
    DraftKings names and checked by arithmetic alone -- the names, DraftKings'
    status, the roster shape, a defense facing a forced offense, a forced
    quarterback with every catcher excluded, both teams on a showdown, and
    the salary with the open slots filled as cheaply as the slate allows.
    `board` reads the tournament board's defense rule (dfs_tourney
    .classic_allowed: no defense facing our quarterback or backs) instead of
    the sheet's (_lineup_ok: none facing anyone we roster).

    A pass is not a promise: the build can still drop a forced man Sleeper
    does not project or the depth chart rules out (it reads Sleeper; this
    does not), and the build's refusal is the final word. `pp` is raw or
    parsed (pick_parse is idempotent); one naming nobody resolves to None."""
    pp = pick_parse(pp)
    if not pp:
        return None, None
    try:
        import nfl_adp
        norm = nfl_adp._norm
    except Exception as e:
        errlog.note("PICK-norm", e)

        def norm(s):
            return " ".join(str(s or "").lower().split())
    ex, fo = set(), set()
    for key, dst in (("exclude", ex), ("force", fo)):
        for e in pp.get(key) or []:
            nm, why = _pk_find(e, rows, norm)
            if why:
                return None, why
            dst.add(nm)
    captain = None
    if pp.get("captain"):
        if mode != "showdown":
            return None, "a captain is a showdown pick: a classic lineup has none"
        captain, why = _pk_find(pp["captain"], rows, norm)
        if why:
            return None, why
        fo.add(captain)
    both = sorted(ex & fo)
    if both:
        return None, f"{both[0]} is both excluded and forced"
    info, cpt_sal = {}, {}
    for r in rows:
        if (r.get("roster_pos") or "").upper() == "CPT":
            cpt_sal[r["name"]] = int(float(r["salary"]))
            info.setdefault(r["name"], r)
        else:
            info[r["name"]] = r
    for nm in sorted(fo):
        if not _playable(info[nm]):
            return None, f"DraftKings lists {nm} {(info[nm].get('status') or '').upper()}: a lineup holding him is dead"

    def pos_of(nm):
        p = (info[nm].get("pos") or "").upper().split("/")[0]
        return "RB" if p == "FB" else p

    def team_of(nm):
        return (info[nm].get("team") or "").strip().upper()
    live = [nm for nm, r in info.items() if _playable(r) and nm not in ex and nm not in fo]
    if mode == "showdown":
        if len(fo) > len(SHOWDOWN_ROSTER):
            return None, f"{len(fo)} forced: a showdown lineup holds {len(SHOWDOWN_ROSTER)}"
        if len(fo) == len(SHOWDOWN_ROSTER) and len({team_of(nm) for nm in fo}) < SHOWDOWN_MIN_TEAMS:
            return None, "six forced players from one team: DraftKings requires both teams in a showdown lineup"

        def csal(nm):
            return cpt_sal.get(nm, int(round(int(float(info[nm]["salary"])) * CPT_MULT)))
        flex = {nm: int(float(info[nm]["salary"])) for nm in info}
        fill = sorted(flex[nm] for nm in live)
        best = None          # a lower bound: DraftKings' both-teams rule is not read here
        for c in ([captain] if captain else sorted(fo) + [None]):
            rest = [nm for nm in fo if nm != c]
            n_open = len(SHOWDOWN_ROSTER) - 1 - len(rest)
            if c is None:           # an unforced captain: the cheapest open man at 1.5x
                if not live or len(live) < n_open + 1:
                    continue
                cc = min(live, key=csal)
                others = sorted(flex[nm] for nm in live if nm != cc)
                cost = csal(cc) + sum(flex[nm] for nm in rest) + sum(others[:n_open])
            else:
                if len(fill) < n_open:
                    continue
                cost = csal(c) + sum(flex[nm] for nm in rest) + sum(fill[:n_open])
            best = cost if best is None else min(best, cost)
        if best is None:
            return None, "not enough players left to fill a showdown lineup around your picks"
        if best > cap:
            return None, (f"your picks cost ${best:,} with the open slots filled as cheaply as this slate allows, "
                          f"over the ${cap:,} cap")
        return Pick(frozenset(ex), frozenset(fo), captain), None
    counts = collections.Counter(pos_of(nm) for nm in fo)
    for p, k in counts.items():
        if p not in _PICK_SHAPE:
            return None, f"{min(nm for nm in fo if pos_of(nm) == p)} is a {p or 'player with no position'}: a classic lineup has no slot for him"
        if k > _PICK_SHAPE[p]:
            if p == "TE":        # DraftKings' rule allows a second at FLEX; this builder's FLEX never takes one
                return None, (f"{k} forced at TE: the builder fills FLEX with a back or a receiver, so its lineups "
                              f"hold one tight end")
            return None, f"{k} forced at {p}: a classic lineup holds at most {_PICK_SHAPE[p]}"
    if counts["RB"] + counts["WR"] > 6:
        return None, "more than six backs and receivers forced: a classic lineup holds two backs, three receivers and a FLEX"
    for d in (nm for nm in fo if pos_of(nm) == "DST"):
        opp = _opp_of(team_of(d), info[d].get("game"))
        for o in sorted(nm for nm in fo if pos_of(nm) != "DST" and (not board or pos_of(nm) in ("QB", "RB"))):
            if opp and team_of(o) == opp:
                return None, (f"the {d} defense plays {o}'s team: the builder never pairs a defense with an "
                              f"offense it faces; drop one")
    if stack:
        for q in (nm for nm in fo if pos_of(nm) == "QB"):
            mates = [nm for nm in live + sorted(fo) if nm != q and team_of(nm) == team_of(q) and pos_of(nm) in ("WR", "TE")]
            if not mates:
                return None, (f"every receiver and tight end of {q}'s team is excluded or out: the build stacks the "
                              f"quarterback with one of them")
    open_slots = list(ROSTER)
    for nm in sorted(fo, key=lambda n: pos_of(n) in FLEX_OK):     # QB / TE / DST first, then backs and receivers
        p = pos_of(nm)
        s = p if p in open_slots else ("FLEX" if p in FLEX_OK and "FLEX" in open_slots else None)
        if s is None:
            return None, f"{nm} does not fit the slots left for a {p}"
        open_slots.remove(s)
    cands = [(nm, int(float(info[nm]["salary"])), _elig(pos_of(nm))) for nm in live]
    fill = _cheapest_fill(open_slots, cands)
    if fill is None:
        return None, "not enough players left to fill a classic lineup around your picks"
    cost = sum(int(float(info[nm]["salary"])) for nm in fo) + fill
    if cost > cap:
        return None, (f"your picks cost ${cost:,} with the open slots filled as cheaply as this slate allows, "
                      f"over the ${cap:,} cap")
    return Pick(frozenset(ex), frozenset(fo), None), None


def _pk_restore(pk, kept, dx, pre_gate, waived):
    """([forced men the depth gate removed by RANK], None), or ([], why). A
    WR4 by the depth chart is the house rule, and the owner's pick beats it:
    he comes back into OUR pool, named in `waived`, never into the field's.
    Any other reason the gate gives (injured, not on a roster) means a dead
    lineup: refused."""
    have = {p["name"] for p in kept}
    out = []
    for d in dx:
        nm = d.get("name")
        if nm not in pk.force or nm in have:
            continue
        why = d.get("why") or ""
        if "by depth chart" not in why:
            return [], f"{nm} cannot be forced: {why}"
        p = pre_gate[nm]
        p["depth"] = why.split(" ")[0]
        out.append(p)
        waived.append(f"the depth chart for {nm} ({why}): in your lineups, never in the field's")
    return out, None


def _sd_pick_waivers(pk, builds, pool, objective):
    """The showdown house rules the owner's own picks break, named: the GPP
    captain rules for his captain, and _sd_allowed's construction rules for
    a forced FLEX beside the captain and the other forced men (the free
    slots still obey them; the forced set is waived by construction)."""
    out = []
    if pk.captain and objective in ("ceiling", "leverage"):
        c = next((p for p in pool if p["name"] == pk.captain), None)
        rich = sum(1 for p in pool if p.get("pos") in _SD_GPP_CPT_POS
                   and p["cpt_salary"] >= _SD_MIN_CPT_SALARY) >= 3
        if c is not None and (c.get("pos") not in _SD_GPP_CPT_POS
                              or (rich and c["cpt_salary"] < _SD_MIN_CPT_SALARY)):
            out.append(f"the tournament captain rule (no kicker or defense; ${_SD_MIN_CPT_SALARY:,}+) for {c['name']}")
    beside = {}
    for cap_p, picked, _sal in builds:
        got = []
        for q in (q for q in picked if "_pk" in q):
            if not _sd_allowed(q, cap_p, got):
                beside.setdefault(q["name"], set()).add(cap_p["name"])
            got.append(q)
    return out + [sd_waiver_text(nm, caps) for nm, caps in sorted(beside.items())]


def sd_waiver_text(name, captains):
    """One forced man's construction-rule waiver, naming every captain it was waived beside (the first versions
    named one: "beside captain Ace Hawk" while he also sat beside Cal Hawk -- round-3 review), four at most."""
    caps = sorted(captains)
    more = f" and {len(caps) - 4} more" if len(caps) > 4 else ""
    return (f"the showdown construction rules for {name} beside captain{'s' if len(caps) > 1 else ''} "
            f"{', '.join(caps[:4])}{more}")


def _pk_what(pk):
    """'holding A, B without C' for a refusal: an exclude-only pick read "holding  under the cap"."""
    parts = ([f"holding {', '.join(sorted(pk.force))}"] if pk.force else []) \
        + ([f"without {', '.join(sorted(pk.exclude))}"] if pk.exclude else [])
    return " ".join(parts) or "for your picks"


def pick_stamp(pk, waived=(), notes=()):
    """What a picked build says it was built under (the card, the board)."""
    return {"exclude": sorted(pk.exclude), "force": sorted(pk.force), "captain": pk.captain,
            "waived": list(waived), "notes": list(notes)}


# ---- The depth-chart gate ---------------------------------------------------
# The owner: "it gave me Theo Wease Jr. for FLEX -- he's not on their depth
# chart anywhere; the Chargers signed him to the practice squad Wednesday. We
# need to be only picking guys that are WR1, 2, and maaaybe WR3." He had no
# Sleeper projection, so he fell to the CSV's own number: DK's 8.6 average
# from another team's season, at $3,000 -- elite value on paper. Two rules,
# REGULAR SEASON ONLY (August's measured inverted-usage model wants the deep
# guys on purpose):
#   * a player the sim never projected is EXCLUDED, not handed a stale
#     average -- an unknown is not a bargain;
#   * the roster has to know him. Sleeper's depth chart (fetched for the
#     draft board anyway, cached 12h). Slot conventions vary by team -- the
#     Bears list Odunze "LWR order 2" and he is their WR2 -- so the rule is
#     a RANK, not a slot: within a team and position, order by depth-chart
#     order then projection and keep the top QB1, RB1-2 (committees are
#     real), WR1-3, TE1-2 (the Chargers list Njoku TE2; a strict TE1 would
#     pre-empt a real name -- the projection decides, the tag shows it);
#     kickers and defenses pass through. No depth entry
#     (a practice-squad player reads as Active with none), Inactive,
#     Out/Doubtful/IR -> out. Questionable stays, tagged, so the owner sees
#     it before he enters.
_DEPTH_KEEP = {"QB": 1, "RB": 2, "WR": 3, "TE": 2}
_INJ_OUT = {"OUT", "DOUBTFUL", "IR", "PUP", "SUS", "SUSPENDED", "NA", "COV"}


class RosterUnavailable(RuntimeError):
    """No trustworthy roster for the depth-chart gate: the live fetch failed and
    no last-known-good copy is within nfl_adp.ROSTER_MAX_AGE_S. Builders refuse
    rather than build a board with the gate silently off (contract of
    2026-09-19; the failure used to be an authoritative empty roster)."""
    def __init__(self, state):
        super().__init__(f"roster unavailable ({state.get('source')}): {state.get('error') or 'no copy'}; "
                         f"last fetched {state.get('fetched_utc') or 'never'}")
        self.state = state


_LAST_ROSTER = {"state": None}


def iso_ts(v):
    """DraftKings' start time as epoch seconds, or None.

    DraftKings writes two shapes and they are NOT the same clock. The contest
    endpoint and the draftables feed carry UTC with a trailing Z
    ("2026-10-18T17:00:00.0000000Z"); the lobby's StartDateEst carries EASTERN
    time with no zone at all ("2026-10-18T13:00:00.0000000" for the same 1:00
    pm kickoff). Both used to be read as UTC, which put every lobby kickoff
    four hours early (five after the clocks go back): pc_worker's pre-lock
    refreshes keyed off the lobby string, so its "last look ~25 minutes before
    kickoff" ran at T-4h25m and no Sunday- or Monday-night showdown board was
    ever rebuilt after the inactives (measured on the week-6 lobby, 2026-10-16:
    dg 154959 SNF read 20:20 UTC against the contest's 00:20Z). A Z or an
    explicit offset is honoured; a string with neither is DraftKings' Eastern.
    Seven fractional digits, which datetime refuses, are dropped."""
    import datetime
    import re
    s = str(v or "").strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?$", s, re.I)
    if not m:
        return None
    try:
        dt = datetime.datetime.fromisoformat(m.group(1))
    except ValueError:
        return None
    zone = (m.group(2) or "").upper()
    if zone == "Z":
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    elif zone:
        sign = 1 if zone[0] == "+" else -1
        hh, mm = int(zone[1:3]), int(zone[-2:])
        dt = dt.replace(tzinfo=datetime.timezone(sign * datetime.timedelta(hours=hh, minutes=mm)))
    else:
        import clock
        dt = dt.replace(tzinfo=clock.ET)
    return int(dt.timestamp())


def seconds_to_lock(starts):
    """Seconds until a DraftKings start string, or None when it is unreadable;
    what the depth-chart gate's roster allowance is set from."""
    import time as _t
    ts = iso_ts(starts)
    return None if ts is None else float(ts) - _t.time()


def _depth_source(seconds_to_lock=None):
    """(records or None, state) from nfl_adp.roster(); a crash in the roster
    machinery itself is ledgered and reads as unavailable."""
    try:
        import nfl_adp
        return nfl_adp.roster(seconds_to_lock=seconds_to_lock)
    except Exception as _e:
        errlog.note("NFLD-depth", _e)
        return None, {"source": "unavailable", "error": f"{type(_e).__name__}: {_e}",
                      "fetched_utc": None, "age_s": None, "records": 0, "max_age_s": None,
                      "seconds_to_lock": seconds_to_lock}


def _depth_records():
    """(records, norm) for the callers that only READ the roster (the rebuild
    trigger's status classes): {} when none is trustworthy, which those callers
    already treat as "say nothing". The gate itself goes through _depth_source."""
    import nfl_adp
    recs, _st = _depth_source()
    return (recs or {}), nfl_adp._norm


def roster_state():
    """The roster stamp a board carries: the state the depth-chart gate actually
    used for this build (source, fetched_utc, age_s, max_age_s, error)."""
    if _LAST_ROSTER["state"] is None:
        _LAST_ROSTER["state"] = _depth_source()[1]
    return dict(_LAST_ROSTER["state"])


def _depth_verdict(p, recs, norm):
    """(ok, why, order, questionable) for one pool player against Sleeper's
    roster. `order` is his depth-chart order (the rank across the team's
    position group is decided in _apply_depth)."""
    pos = (p.get("pos") or "").upper()
    if pos not in _DEPTH_KEEP:
        return True, None, None, False          # K / DST: no depth to check
    if norm is None or not recs:
        return True, None, None, False          # no roster source: say nothing
    key = norm(p.get("name") or "")
    rec = recs.get(key) or recs.get(key.replace(" ", ""))
    if rec is None:
        return False, "not on any roster Sleeper knows", None, False
    if (rec.get("status") or "Active") != "Active" or rec.get("active") is False:
        return False, f"roster status {rec.get('status') or 'inactive'}", None, False
    inj = (rec.get("injury") or "").upper()
    if inj in _INJ_OUT:
        return False, f"listed {rec.get('injury')}", None, False
    d = rec.get("depth")
    if d is None:
        return False, "not on the depth chart (practice squad / inactive)", None, False
    return True, None, int(d), inj == "QUESTIONABLE"


def _apply_depth(players, preseason, seconds_to_lock=None):
    """Split a pool into (kept, excluded) by the roster gate. Within each
    team's position group the survivors are RANKED -- depth-chart order
    first, projection second -- and only the top _DEPTH_KEEP stay, tagged
    WR1/WR2/WR3, RB1/RB2, QB1, TE1/TE2 (a Questionable player carries ·Q).
    That rank is what the owner means by "WR1, 2 and maybe 3", and it
    survives every team's slot convention."""
    if preseason:
        return players, []
    import nfl_adp
    recs, state = _depth_source(seconds_to_lock)
    _LAST_ROSTER["state"] = state
    if recs is None:
        # the contract: never build with the gate silently off. Ledger it under
        # a stable id and let the builder refuse (None / {"error"}), the same
        # shape as TOURN-capacity, so one Sleeper outage cannot kill the PC loop
        errlog.note("NFLD-roster", msg=f"no trustworthy roster for the depth-chart gate "
                                       f"({state.get('source')}: {state.get('error')}); the build is refused "
                                       f"rather than made with the gate off", path=state.get("fetched_utc"))
        raise RosterUnavailable(state)
    norm = nfl_adp._norm
    kept, excluded, groups = [], [], {}
    for p in players:
        ok, why, order, q = _depth_verdict(p, recs, norm)
        if not ok:
            excluded.append({"name": p["name"], "pos": p["pos"],
                             "team": p.get("team"), "why": why})
            continue
        pos = (p.get("pos") or "").upper()
        if order is None:                       # K / DST, or no roster source
            p["depth"] = pos if pos not in _DEPTH_KEEP else None
            kept.append(p)
            continue
        # Questionable on EITHER source: DK listed Tyler Warren Q on the week-6
        # main slate while Sleeper had nothing, and the sheet tagged him a
        # plain TE1
        p["_order"], p["_q"] = order, q or (p.get("dk_status") or "") in ("Q", "QUESTIONABLE")
        groups.setdefault((p.get("team"), pos), []).append(p)
    for (_tm, pos), grp in groups.items():
        grp.sort(key=lambda x: (x["_order"], -(x.get("proj") or 0)))
        keep_n = _DEPTH_KEEP[pos]
        for i, p in enumerate(grp):
            rank = i + 1
            if rank > keep_n:
                excluded.append({"name": p["name"], "pos": p["pos"],
                                 "team": p.get("team"),
                                 "why": f"{pos}{rank} by depth chart (keep {pos}1-{keep_n})"
                                        if keep_n > 1 else
                                        f"{pos}{rank} by depth chart (keep {pos}1)"})
                continue
            p["depth"] = f"{pos}{rank}" + ("·Q" if p["_q"] else "")
            kept.append(p)
        for p in grp:
            p.pop("_order", None)
            p.pop("_q", None)
    return kept, excluded


def _norm_index(pool):
    """{normalised name: entry} for the projection pool.

    Sleeper builds its name as first + last, which drops the suffix; DK keeps it
    and punctuates initials. So "Marvin Harrison Jr." never equalled "Marvin
    Harrison", "A.J. Dillon" never equalled "AJ Dillon", and roughly a third of a
    slate missed on an exact-string compare -- falling back to DK's
    AvgPointsPerGame, which is a REGULAR-SEASON number and the single thing the
    preseason model exists to override. It made a starter who will play one
    series the highest projection on the board.

    nfl_adp._norm already strips accents, suffixes, punctuation and case for
    exactly this reason; it simply was not being used here."""
    try:
        import nfl_adp
        norm = nfl_adp._norm
    except Exception:
        return {}, None
    idx = {}
    for k, v in pool.items():
        nk = norm(k)
        # First writer wins: a real player must never be displaced by a defense
        # that happens to normalise alike.
        if nk and nk not in idx:
            idx[nk] = v
        # Punctuated initials survive _norm as separate tokens -- "A.J. Dillon"
        # becomes "a j dillon" while Sleeper's "AJ Dillon" becomes "aj dillon",
        # so the two still miss. Squeezing the spaces out makes them the same
        # key. Kept as a SECOND key rather than replacing the first, so the
        # looser form is only reached when the ordinary one finds nothing.
        sk = nk.replace(" ", "")
        if sk and sk not in idx:
            idx[sk] = v
    return idx, norm


def _pool_match(pool, name, pos, team, nidx=None, norm=None):
    """This player's simulated projection, or None.

    The team-abbreviation lookup exists for ONE reason: DK writes a defense as
    "Panthers" or "CAR" and the pool registers it under both. It was applied to
    every player, so anyone whose NAME was not in the pool silently inherited his
    team's DEFENSE -- projection, ceiling, floor and sample array alike.

    On a single preseason board that was 26 players. Feleipe Franks, a fifth-
    string tight end, was not being projected as a tight end at all; his
    projection WAS the Carolina defense. And because they all took the same array
    OBJECT, a lineup holding four of them looked like it had enormous upside --
    they boomed in perfect lockstep, since they were the same numbers. That is
    the tail the ceiling objective was buying.

    Nothing warned, either: `unmatched` stayed empty because a match had been
    found. A player the model deliberately left out of the pool (beyond the
    measured exhibition depth at his position) must come back as unmatched and be
    priced as the fringe player he is."""
    hit = pool.get(name)
    if hit is not None:
        return hit
    if nidx and norm:
        nk = norm(name)
        want = (pos or "").upper()
        for key in (nk, nk.replace(" ", "")):
            hit = nidx.get(key)
            # Only across the same position -- normalising must not turn a
            # missing tight end into somebody who shares a stripped name.
            if hit is not None and (hit.get("pos") or "").upper() == want:
                return hit
    if (pos or "").upper() == "DST" and team:
        hit = pool.get(team)
        if hit is not None and hit.get("pos") == "DST":
            return hit
    return None


def _deep_fallback(pool, preseason):
    """{pos: projection} for a preseason player the depth model left out.

    Being excluded must not be an UPGRADE, and it was. A matched player carries a
    preseason projection -- two or three points, because exhibition usage is
    small. An unmatched one fell back to DK's AvgPointsPerGame, a REGULAR-SEASON
    average worth seven to ten. So every player the model deliberately judged too
    deep to see the field came back rated three times higher than the players it
    had modelled, and one of them turned up as the captain.

    A player beyond the measured exhibition depth at his position cannot
    reasonably be projected above the LAST player who made it in, so that is what
    he gets. Regular season keeps DK's average, where it means what it says."""
    if not preseason:
        return {}
    by = {}
    for v in pool.values():
        p = (v.get("pos") or "").upper()
        if p in ("QB", "RB", "WR", "TE") and v.get("proj") is not None:
            by.setdefault(p, []).append(float(v["proj"]))
    return {p: min(vals) for p, vals in by.items() if vals}


def _status_seen(csv_players):
    """Did this paste actually carry DK's Status column?

    A filter that cannot see its input is not a filter, and it fails SILENTLY --
    every OUT and IR player just quietly becomes eligible. On a preseason slate
    that is close to guaranteed damage, because August rosters are full of them.
    So the answer travels to the UI rather than being assumed."""
    return any((c.get("status") or "").strip() for c in csv_players)


def showdown_pool(csv_players):
    """[{name, pos, team, salary, cpt_salary, proj}] -- one entry per PLAYER.

    The export lists everyone twice, so a naive parse doubles the pool and lets
    the same player be rostered as both his CPT and his FLEX self. Keyed by name
    with both salaries attached instead."""
    base, cpt_sal = {}, {}
    for c in csv_players:
        if not _playable(c):
            continue
        nm = c["name"]
        if (c.get("roster_pos") or "").upper() == "CPT":
            cpt_sal[nm] = int(c["salary"])
            base.setdefault(nm, c)
        else:
            base[nm] = c
    out = []
    for nm, c in base.items():
        pos = (c.get("pos") or "").upper().split("/")[0]
        pos = "RB" if pos == "FB" else pos
        if pos not in SHOWDOWN_POS:
            continue
        flex = int(c["salary"])
        out.append({"name": nm, "pos": pos, "team": (c.get("team") or "").strip(),
                    "salary": flex,
                    # If only the CPT row survived, its salary IS the flex one
                    # scaled up; and if only FLEX rows exist, DK's captain price
                    # is 1.5x by rule.
                    "cpt_salary": cpt_sal.get(nm, int(round(flex * CPT_MULT))),
                    "proj": float(c.get("proj") or 0.0),
                    "dk_status": (c.get("status") or "").upper()})
    return out


def _value_sd(p, objective):
    """Showdown's per-player value. Same as _value except that leverage's
    ownership discount applies to a DEFENSE too. Classic exempts the DST slot
    (its score range is too small for contrarianism to differentiate a
    lineup); in showdown a defense competes for the captain's 1.5x and for
    five open FLEX slots against every skill player, and exempting it there
    handed both defenses a free pass past a discount every stud paid: with
    nine players at the 45% ownership cap the leverage build on the 2026
    opener was the Seahawks defense as captain beside both quarterbacks and
    the Patriots defense -- on the joint rule it scored 80.4 against 75.0 for
    the same lineup with the discount applied, and 76.2 for a Maye stack."""
    if objective == "leverage":
        v = p["ceiling"] * (1.0 - 0.007 * p.get("own", 8.0))
        return v + _PK_BIAS if "_pk" in p else v
    return _value(p, objective)


# Construction rules the showdown record supports (Establish The Run's
# year-in-review of top-1% lineups): a defense beside the OPPOSING team's
# captain is the one pairing the sharp field has learned to avoid -- captain
# QB with the other defense fell from 7.8% of lineups to 1.4% in a year --
# because the defense scores exactly when the captain's offense stalls; more
# than two kickers/defenses in a lineup finished top-1% at a 1.1% rate; and
# no top finisher carried more than two $200-$2,000 punts. The optimizer used
# to hand back Smith-Njigba as captain with Darnold AND the Patriots defense,
# a lineup that needs Seattle's offense to score and to stall in the same
# game; the joint sim discounted it, the leverage discount's cheap filler
# outweighed that, and a rule is cheaper than a fight between two heuristics.
_SD_MAX_KDST = 2
_SD_MAX_PUNTS = 2
_SD_PUNT_SALARY = 2000
# And no kicker or defense as CAPTAIN in a GPP build: "outlier D/ST
# performances are difficult to predict and higher-than-expected ownership
# often occurs when these positions are targeted; in large-field tournaments
# it's acceptable to remove them from the captain player pool" (ETR). Wide
# receivers captained 31.4% of top-1% lineups; the average winning captain
# cost about $13,000. Denied the opposing defense at FLEX, leverage promptly
# captained the Seahawks kicker at 16.8% ownership beside both quarterbacks
# (projected 88 against 95 for the stacks; the ownership estimate saturates
# on a one-game pool, so the discount rewards whoever it cannot cap), and
# ceiling tied a defense captain with a Maye stack on the joint rule. Cash
# keeps the full pool: a defense captain by projection is a different bet.
_SD_GPP_CPT_POS = {"QB", "RB", "WR", "TE"}
# And a GPP captain is a real price: the winning captain paid at least
# $11,400 in 17 of 19 slates ETR reviewed, averaging about $13,000-$14,800.
# A cheap captain is what the cap arithmetic likes (it frees room for every
# stud at FLEX) and what the record says does not win: on the opener the
# sim's second entry captained a $6,900 tight end beside both quarterbacks.
# Applied only when the pool offers three captains at the price, so a
# flat-priced exhibition slate is not emptied.
_SD_MIN_CPT_SALARY = 11000
# The owner, on the first entries: "It's also not smart to have your leg
# people both the same position for the same team. Barner and Arroyo are
# both tight ends for the Seahawks ... adding the barely used 2nd string
# pass blocking TE is not the best idea." Two tight ends from one team
# split one role -- unlike two receivers, who can both be a quarterback's
# first read on different drives -- so one per team. And a punt-priced
# player is only a play when he has a job: a second-string tight end is a
# blocker with a touchdown lottery ticket, and the roster gate keeps him in
# the pool (a TE2 can be a real name at a real price) without making him a
# punt. Punts must carry a depth tag of RB1-2, WR1-3 or TE1.
_SD_MAX_TE_PER_TEAM = 1
_SD_PUNT_ROLES = {"RB1", "RB2", "WR1", "WR2", "WR3", "TE1"}


def _sd_allowed(p, cap_p, picked):
    """May `p` fill a FLEX beside captain `cap_p` and the FLEX `picked` so far?"""
    pos = p.get("pos")
    if pos == "DST" and cap_p.get("pos") != "DST" and p.get("team") \
            and p.get("team") != cap_p.get("team"):
        return False                        # the captain's opponents' defense
    if pos in ("K", "DST"):
        n = (1 if cap_p.get("pos") in ("K", "DST") else 0) \
            + sum(1 for q in picked if q.get("pos") in ("K", "DST"))
        if n >= _SD_MAX_KDST:
            return False
    if p["salary"] <= _SD_PUNT_SALARY:
        n = (1 if cap_p.get("salary", 0) <= _SD_PUNT_SALARY else 0) \
            + sum(1 for q in picked if q["salary"] <= _SD_PUNT_SALARY)
        if n >= _SD_MAX_PUNTS:
            return False
        # A punt needs a role. Rows carry the roster gate's tag (WR3, TE2,
        # RB1·Q ...); one the gate did not tag (no roster source) passes.
        tag = (p.get("depth") or "").split("·")[0]
        if tag and tag not in _SD_PUNT_ROLES:
            return False
    if pos == "TE":
        n = (1 if cap_p.get("pos") == "TE" and cap_p.get("team") == p.get("team") else 0) \
            + sum(1 for q in picked if q.get("pos") == "TE" and q.get("team") == p.get("team"))
        if n >= _SD_MAX_TE_PER_TEAM:
            return False
    return True


def _sd_fill(cands, budget, objective, rng, cpt_team, greedy=False, cap_p=None, seed=None):
    """Five FLEX under `budget`, spanning both teams with the captain, under
    the construction rules (_sd_allowed) when the captain is given. `seed`
    (the picker's forced FLEX, never among `cands`) fills the first slots: the
    rules then bind the free slots only, the forced men COUNTING against them
    (a forced punt leaves one punt to the rest), and _sd_allowed itself is
    untouched -- its five rejections are pinned (S5)."""
    picked, sal, teams = [], 0, {cpt_team}
    if seed:
        picked = list(seed)
        sal = sum(p["salary"] for p in seed)
        if sal > budget or len(picked) > 5:
            return None
        teams |= {p["team"] for p in seed if p["team"]}
    # A fill that dies mid-way (no player fits the last slot) used to return
    # with its partial picks still flagged taken, and they stayed taken for
    # every later fill under that captain -- so the expensive players, who
    # are the ones a dead fill has usually already spent, vanished from the
    # search after the first failure. On the 2026 opener a Maye-captain
    # search scored 290 candidates and not one carried Smith-Njigba; the
    # best lineup it never saw scored 108.8 on the joint rule against the
    # 99.5 it returned. The flags are released on every path now.
    try:
        for slot in range(len(picked), 5):
            pool = [p for p in cands if p["_free"] and sal + p["salary"] <= budget
                    and (cap_p is None or _sd_allowed(p, cap_p, picked))]
            # Last slot and still one-sided -> it has to come from the other team.
            if slot == 4 and len(teams) < SHOWDOWN_MIN_TEAMS:
                other = [p for p in pool if p["team"] and p["team"] not in teams]
                if not other:
                    return None
                pool = other
            if not pool:
                return None
            if greedy:
                pick = max(pool, key=lambda p: _value_sd(p, objective))
            else:
                top = sorted(pool, key=lambda p: -_value_sd(p, objective))[:16]
                w = [max(0.1, _value_sd(x, objective)) ** 2 for x in top]
                pick = rng.choices(top, weights=w)[0]
            pick["_free"] = False
            picked.append(pick)
            sal += pick["salary"]
            if pick["team"]:
                teams.add(pick["team"])
    finally:
        for p in picked:
            p["_free"] = True
    if len(teams) < SHOWDOWN_MIN_TEAMS:
        return None
    return picked, sal


# The GPP objectives (ceiling, leverage) score a candidate lineup on its JOINT
# simulated points -- half its 90th percentile, half its median, the captain's
# 1.5x and (for leverage) each player's ownership discount applied per player
# -- exactly the rule the racing captain optimizer took on after Monza. The
# sum of six players' own ceilings is a total no simulated game produces, and
# it buys the fattest tail per dollar whatever it correlates with: on the 2026
# opener (NE @ SEA) the sum-of-ceilings build was the Seahawks DEFENSE as
# captain beside both quarterbacks and the other defense, a lineup whose
# parts score in different games. Joint scoring is what knows that.
# Search width. Sixty randomized fills per captain left the answer seed-
# dependent (two seeds on the opener gave two different leverage builds,
# one of them the weaker on every number); a one-game pool is ~20 players,
# so four hundred fills per captain cost a few seconds and the seeds agree.
_SD_RESTARTS = 400
_JOINT_KEEP = 600          # candidates re-scored jointly per build
_JOINT_ITERS = 2500        # iterations sampled per candidate (pure Python)


def _joint_score(cap_p, picked, objective, iters):
    """0.5 x p90 + 0.5 x median of the lineup's simulated totals over `iters`
    iteration indexes, captain at 1.5x, leverage's ownership discount per
    player. None when any row carries no simulated array."""
    rows = [(CPT_MULT, cap_p)] + [(1.0, p) for p in picked]
    if any(not p.get("arr") for _m, p in rows):
        return None
    disc = []
    for m, p in rows:
        w = m
        if objective == "leverage":             # a defense pays it too (_value_sd)
            w *= (1.0 - 0.007 * p.get("own", 8.0))
        disc.append((w, p["arr"]))
    L = min(len(a) for _w, a in disc)
    tot = []
    for i in iters:
        j = i % L
        tot.append(sum(w * a[j] for w, a in disc))
    tot.sort()
    return 0.5 * tot[int(0.9 * len(tot))] + 0.5 * tot[len(tot) // 2]


# Candidates re-scored jointly PER CAPTAIN. A multi-entry in a big showdown
# spreads captains across entries (one build per captain), so the search
# keeps the best lineup for every captain rather than one global winner.
_JOINT_PER_CPT = 40


def _showdown_bests(players, cap, objective, restarts=_SD_RESTARTS, rng=None, pick=None, per_captain=1):
    """[(score, cap_p, picked, sal), ...] -- the best lineup for EACH captain,
    best first. Every player is tried as captain (the pool is one game, so
    that is only a few dozen); each captaincy gets one greedy fill plus a
    batch of randomized ones, under the construction rules. Cash
    (projection) ranks on the additive sum; the GPP objectives re-score each
    captain's strongest candidates on their joint sims (_joint_score).

    `pick` (the players carry the picker's marks): a forced captain is the
    only one tried, the GPP captain rules waived for him; the forced FLEX seed
    every fill (_sd_fill) -- a forced man tried as captain leaves the seed for
    that captaincy, and `others` never holds a forced man, so none is drawn
    twice. Every captaincy of a picked build draws twice the fills, so a pick
    with no forced captain costs about twice the unpicked search (seconds,
    inline in the web worker as the sheet always was). `per_captain` > 1
    returns that many DISTINCT fills of each captain (a forced captain's
    multi-entry: one captain would otherwise be one entry)."""
    rng = rng or random
    if len(players) < len(SHOWDOWN_ROSTER):
        return []
    for p in players:
        p["_free"] = True
    gpp = objective in ("ceiling", "leverage")
    rich = sum(1 for p in players if p.get("pos") in _SD_GPP_CPT_POS
               and p["cpt_salary"] >= _SD_MIN_CPT_SALARY) >= 3
    forced = [p for p in players if "_pk" in p] if pick else []
    per = {}                                    # captain name -> {frozenset: cand}
    for cap_p in players:
        if pick and pick.captain and cap_p["name"] != pick.captain:
            continue                        # the owner's captain is the only one
        budget = cap - cap_p["cpt_salary"]
        if budget < 0:
            continue
        mine = bool(pick and pick.captain)  # his captain: the GPP rules below are waived for him
        if gpp and not mine and cap_p.get("pos") not in _SD_GPP_CPT_POS:
            continue                        # no K / DST captain in a GPP build
        if gpp and not mine and rich and cap_p["cpt_salary"] < _SD_MIN_CPT_SALARY:
            continue                        # a GPP captain is a real price
        seed = [p for p in forced if p is not cap_p]
        if len(seed) > 5:
            continue
        cap_p["_free"] = False
        others = [p for p in players if p is not cap_p and not (forced and "_pk" in p)]
        cap_val = CPT_MULT * _value_sd(cap_p, objective)
        if "_pk" in cap_p:
            # the bias counts ONCE wherever a forced man sits: at 1.5x his
            # captaincies outranked every other captain whatever they scored
            cap_val -= (CPT_MULT - 1.0) * _PK_BIAS
        cands = per.setdefault(cap_p["name"], {})
        # a picked captaincy draws twice the fills: with fewer free slots each
        # fill is cheaper, and at the default count one random pick in sixty
        # came back 0.2 points short of the brute-force optimum where the
        # unpicked search was exact on all sixty (2026-10-30)
        for i in range((2 * restarts if pick else restarts) + 1):
            r = _sd_fill(others, budget, objective, rng, cap_p["team"], greedy=(i == 0),
                         cap_p=cap_p, seed=seed or None)
            if not r:
                continue
            picked, sal = r
            score = cap_val + sum(_value_sd(p, objective) for p in picked)
            key = frozenset(p["name"] for p in picked)
            if key not in cands or score > cands[key][0]:
                cands[key] = (score, cap_p, list(picked), sal + cap_p["cpt_salary"])
        cap_p["_free"] = True
    out = []
    for _nm, cands in per.items():
        if not cands:
            continue
        ranked = sorted(cands.values(), key=lambda c: -c[0])
        if per_captain > 1:
            top = ranked[:max(_JOINT_PER_CPT, per_captain)]
            L = min((len(p.get("arr") or []) for c in top for p in [c[1]] + c[2]), default=0) if gpp else 0
            if L > 0:
                step = max(1, L // _JOINT_ITERS)
                js = [(_joint_score(c[1], c[2], objective, range(0, L, step)), c) for c in top]
                top = [(v, c[1], c[2], c[3]) for v, c in sorted((x for x in js if x[0] is not None),
                                                                 key=lambda x: -x[0])]
            out.extend(top[:per_captain])
            continue
        best = ranked[0]
        if gpp:
            top = ranked[:_JOINT_PER_CPT]
            L = min((len(p.get("arr") or []) for c in top for p in [c[1]] + c[2]), default=0)
            if L > 0:
                step = max(1, L // _JOINT_ITERS)
                iters = range(0, L, step)
                jbest = None
                for c in top:
                    js = _joint_score(c[1], c[2], objective, iters)
                    if js is not None and (jbest is None or js > jbest[0]):
                        jbest = (js, c[1], c[2], c[3])
                if jbest is not None:
                    best = jbest
        out.append(best)
    out.sort(key=lambda c: -c[0])
    return out


def optimize_showdown(players, cap, objective, restarts=_SD_RESTARTS, rng=None):
    """Best 1 CPT + 5 FLEX under the cap (see _showdown_bests)."""
    per = _showdown_bests(players, cap, objective, restarts=restarts, rng=rng)
    if not per:
        return None
    _score, cap_p, picked, sal = per[0]
    return cap_p, picked, sal


def optimize_showdown_multi(players, cap, objective, n, restarts=_SD_RESTARTS, rng=None, pick=None):
    """The best lineup for each of the top `n` captains, best captain first --
    a multi-entry that spreads captains, which is how a big showdown is
    played: "be a rare lineup, not just rare players". With the picker's
    captain there is one captain, so the entries are his `n` best distinct
    fills (measured before: n = 5 came back one entry)."""
    per = _showdown_bests(players, cap, objective, restarts=restarts, rng=rng, pick=pick,
                          per_captain=(max(1, int(n)) if pick and pick.captain else 1))
    return [(c[1], c[2], c[3]) for c in per[:max(1, int(n))]]


def _by_pos(players):
    by = {pos: [] for pos in set(ROSTER)}
    for p in players:
        for slot in p["elig"]:
            if slot in by:
                by[slot].append(p)
    for pos in by:
        by[pos].sort(key=lambda p: -p["proj"])
    if any(len(by[pos]) < ROSTER.count(pos) for pos in set(ROSTER)):
        return None
    return by


def _reserve(asc, slots, used):
    """The cheapest salary that could fill `slots` from players not in `used`
    (each man once, specific slots before the FLEX), or None when a slot has
    no one: a lower bound -- the stack and defense rules are not read."""
    tot, took = 0, set()
    for s in sorted(slots, key=lambda s: s == "FLEX"):
        for p in asc[s]:
            if p["name"] not in used and p["name"] not in took:
                took.add(p["name"])
                tot += p["salary"]
                break
        else:
            return None
    return tot


def _build_one(by_pos, cap, objective, stack_team=None, stack_min=0, rng=random,
               bring_min=0, fixed=None, cheapest=False, asc=None):
    """One greedy-randomized valid lineup. With a stack, the QB slot is HELD to
    the stack team (catchers without their quarterback are not a stack) and
    WR/TE/FLEX slots seed from his team so they boom together. `bring_min`
    additionally requires a pass-catcher from the QB's OPPONENT -- the
    bring-back: a shootout lifts both sides, and the game-stack owns it.

    `fixed` (the picker's forced players) are SEATED first, the quarterback
    before the rest so the stack and the bring-back read off him: QB, TE and
    DST in their own slots, a back or receiver in his position's slot and
    then the FLEX. Every fill then starts from them with the budget they
    leave. Biasing their value alone was not enough: the fill admits a player
    whenever he fits the cap with nothing held back for the open slots, so
    the owner's own shape (a quarterback and two of his receivers) under the
    projection objective completed 0 of 2,000 fills and the search answered
    "no valid lineup" beside a legal one at 124.3 (Fable, 2026-10-30).
    `cheapest` fills every open slot with its cheapest legal player: the
    picked search's deterministic start. `asc` (by_pos sorted by salary)
    holds every pick of a picked fill to what leaves the open slots their
    cheapest fill (_reserve): with $22,000 forced, the fills -- the cheapest
    one too, whose bring-back went to a $4,200 tight end ahead of a $3,500
    receiver -- died at the FLEX with $47,100 spent and no one under $3,500.
    A picked fill also chooses its quarterback, his stack and the bring-back
    BEFORE the slots, each where the money left allows: left to the slot
    order the tight end's slot always took the stack, so a lineup whose
    bring-back had to be the tight end was never built (a receiver, a back and
    a receiver forced on three teams: no fill of 2,000 beside a legal 222.3).
    The unpicked fill is untouched (`asc` None)."""
    used, lineup, sal, stacked, brought = set(), [], 0, 0, 0
    stack_opp = None
    dst_opps, off_teams = set(), set()
    taken = set()

    def slot_for(p):
        si = next((i for i in range(len(ROSTER)) if i not in taken and ROSTER[i] == p["pos"]), None)
        if si is None and p["pos"] in FLEX_OK:
            si = next((i for i in range(len(ROSTER)) if i not in taken and ROSTER[i] == "FLEX"), None)
        return si

    def seat(p, si):
        nonlocal sal, stacked, brought, stack_opp
        taken.add(si)
        if stack_team and p["pos"] == "QB":
            stack_opp = p.get("opp")
            # a forced receiver of his opponent seated before him is already the bring-back
            brought = sum(1 for q in lineup if q.get("team") == stack_opp and q["pos"] in ("WR", "TE"))
        if stack_team and p.get("team") == stack_team and p["pos"] in ("WR", "TE"):
            stacked += 1
        if stack_opp and p.get("team") == stack_opp and p["pos"] in ("WR", "TE"):
            brought += 1
        used.add(p["name"]); lineup.append(p); sal += p["salary"]
        if p["pos"] == "DST":
            if p.get("opp"):
                dst_opps.add(p["opp"])
        elif p.get("team"):
            off_teams.add((p["team"] or "").upper())

    for p in sorted(fixed or (), key=lambda x: x["pos"] != "QB"):
        si = slot_for(p)
        if si is None or (p["pos"] == "QB" and stack_team and p.get("team") != stack_team):
            return None
        seat(p, si)
    if taken and (sal > cap or any((p.get("team") or "").upper() in dst_opps
                                   for p in lineup if p["pos"] != "DST")):
        return None
    if asc is not None and stack_team:
        def choose(cands):
            ok = []
            for c in cands:
                si = slot_for(c)
                if si is None:
                    continue
                rest = _reserve(asc, [ROSTER[j] for j in range(len(ROSTER)) if j not in taken and j != si],
                                used | {c["name"]})
                if rest is not None and sal + c["salary"] + rest <= cap:
                    ok.append((c, si))
            if not ok:
                return None
            if cheapest:
                return min(ok, key=lambda x: (x[0]["salary"], -x[0]["proj"]))
            top = ok[:14]
            return rng.choices(top, weights=[max(0.1, _value(c, objective)) ** 2 for c, _si in top])[0]
        if not any(p["pos"] == "QB" for p in lineup):
            got = choose([p for p in by_pos["QB"] if p.get("team") == stack_team and p["name"] not in used])
            if got is None:
                return None
            seat(*got)
        while stacked < stack_min or (bring_min and brought < bring_min):
            team = stack_team if stacked < stack_min else stack_opp
            got = choose([p for p in by_pos["WR"] + by_pos["TE"] if team and p.get("team") == team
                          and p["name"] not in used and (p.get("team") or "").upper() not in dst_opps])
            if got is None:
                return None
            seat(*got)
    order = sorted(range(len(ROSTER)), key=lambda i: len(by_pos[ROSTER[i]]))
    for k, si in enumerate(order):
        if si in taken:
            continue
        pos = ROSTER[si]
        pool = [p for p in by_pos[pos] if p["name"] not in used and sal + p["salary"] <= cap]
        if asc is not None:
            rest = _reserve(asc, [ROSTER[j] for j in order[k + 1:] if j not in taken], used)
            if rest is None:
                return None
            pool = [p for p in pool if sal + p["salary"] + rest <= cap]
        # NEVER oppose your own defense: a DST scores off the exact failures
        # your opposing skill player needs to succeed. DST slots skip defenses
        # facing a rostered offense; offense slots skip players the rostered
        # DST is trying to shut out. Both directions, because the slot order
        # here is smallest-pool-first and DST usually goes FIRST.
        if pos == "DST":
            pool = [p for p in pool
                    if not (p.get("opp") and p["opp"] in off_teams)]
        else:
            pool = [p for p in pool
                    if (p.get("team") or "").upper() not in dst_opps]
        if not pool:
            return None
        cand = pool
        # A real NFL stack is QB + a pass-catcher (WR/TE) from his team -- that's
        # where the sim's correlation lives (a QB's day lifts his receivers).
        if stack_team and pos == "QB":
            qb_pool = [p for p in pool if p.get("team") == stack_team]
            if not qb_pool:
                return None
            cand = qb_pool
        elif stack_team and pos in ("WR", "TE", "FLEX") and stacked < stack_min:
            team_pool = [p for p in pool if p.get("team") == stack_team and p["pos"] in ("WR", "TE")]
            if team_pool:
                cand = team_pool
        elif (bring_min and stack_opp and pos in ("WR", "TE", "FLEX")
              and stacked >= stack_min and brought < bring_min):
            bpool = [p for p in pool if p.get("team") == stack_opp and p["pos"] in ("WR", "TE")]
            if bpool:
                cand = bpool
        if cheapest:
            pick = min(cand, key=lambda x: (x["salary"], -x["proj"]))
        else:
            top = cand[:14]
            weights = [max(0.1, _value(x, objective)) ** 2 for x in top]
            pick = rng.choices(top, weights=weights)[0]
        if stack_team and pick["pos"] == "QB":
            stack_opp = pick.get("opp")
        if stack_team and pick.get("team") == stack_team and pick["pos"] in ("WR", "TE"):
            stacked += 1
        if stack_opp and pick.get("team") == stack_opp and pick["pos"] in ("WR", "TE"):
            brought += 1
        used.add(pick["name"]); lineup.append(pick); sal += pick["salary"]
        if pick["pos"] == "DST":
            if pick.get("opp"):
                dst_opps.add(pick["opp"])
        elif pick.get("team"):
            off_teams.add((pick["team"] or "").upper())
    if (len(lineup) != len(ROSTER) or sal > cap
            or (stack_team and stacked < stack_min)
            or (bring_min and brought < bring_min)):
        return None
    return lineup, sal


def _lineup_ok(lu, cap, stack_min=0, bring_min=0, exclude=None):
    """Every rule _build_one enforces, on a finished lineup: the roster shape
    (QB, DST and TE exactly one -- a TE is never the FLEX here -- RB 2-3 and WR
    3-4 for seven skill players), the cap, the QB's stack and bring-back, no
    player facing the lineup's own defense, and the portfolio's overlap rule."""
    if len(lu) != len(ROSTER) or len({p["name"] for p in lu}) != len(ROSTER):
        return False
    if sum(p["salary"] for p in lu) > cap:
        return False
    n = {"QB": 0, "RB": 0, "WR": 0, "TE": 0, "DST": 0}
    for p in lu:
        if p["pos"] not in n:
            return False
        n[p["pos"]] += 1
    if not (n["QB"] == 1 and n["DST"] == 1 and n["TE"] == 1 and 2 <= n["RB"] <= 3
            and 3 <= n["WR"] <= 4 and n["RB"] + n["WR"] == 6):
        return False
    dst = next(p for p in lu if p["pos"] == "DST")
    if dst.get("opp") and any((p.get("team") or "").upper() == dst["opp"].upper()
                              for p in lu if p["pos"] != "DST"):
        return False
    qb = next(p for p in lu if p["pos"] == "QB")
    if stack_min and sum(1 for p in lu if p["pos"] in ("WR", "TE") and p.get("team") == qb.get("team")) < stack_min:
        return False
    if bring_min and (not qb.get("opp") or sum(1 for p in lu if p["pos"] in ("WR", "TE")
                                                 and p.get("team") == qb["opp"]) < bring_min):
        return False
    if exclude:
        names = frozenset(p["name"] for p in lu)
        if any(len(names & x) > len(ROSTER) - exclude[1] for x in exclude[0]):
            return False
    return True


def _polish(lu, players, objective, cap, stack_min=0, bring_min=0, exclude=None):
    """Hill-climb a valid lineup to a local optimum of the objective by
    one-player and two-player swaps that keep every rule (_lineup_ok).

    The random fills alone stopped wherever a fill landed: on the week-6 main
    slate (2026-10-16) six builds of the SAME pool scored 219.2-221.7 on the
    ceiling objective against an exact optimum of 229.2 (an integer program
    over the same rules, research-side), and 136.9-139.7 against 145.8 on
    projection -- 3 to 6% short of the sheet's own objective, every build."""
    val = {p["name"]: _value(p, objective) for p in players}
    by_pos = {}
    for p in players:
        by_pos.setdefault(p["pos"], []).append(p)
    for v in by_pos.values():
        v.sort(key=lambda p: -val[p["name"]])
    alt = {"QB": ("QB",), "DST": ("DST",), "TE": ("TE",), "RB": ("RB", "WR"), "WR": ("WR", "RB")}
    sal = {p["name"]: p["salary"] for p in players}
    cur = list(lu)
    cur_v = sum(val[p["name"]] for p in cur)

    def shape_ok(n_rb, n_wr):
        return 2 <= n_rb <= 3 and 3 <= n_wr <= 4
    improved = True
    while improved:
        improved = False
        names = {p["name"] for p in cur}
        cur_sal = sum(p["salary"] for p in cur)
        n_rb = sum(1 for p in cur if p["pos"] == "RB")
        n_wr = sum(1 for p in cur if p["pos"] == "WR")
        best = None
        for i, out in enumerate(cur):                       # one-player swaps: the best one
            for pos in alt[out["pos"]]:
                for c in by_pos.get(pos, ()):
                    gain = val[c["name"]] - val[out["name"]]
                    if gain <= 1e-9 or (best and gain <= best[0]):
                        break
                    if c["name"] in names or cur_sal - out["salary"] + sal[c["name"]] > cap:
                        continue
                    if pos != out["pos"] and not shape_ok(n_rb + (pos == "RB") - (out["pos"] == "RB"),
                                                          n_wr + (pos == "WR") - (out["pos"] == "WR")):
                        continue
                    new = cur[:i] + [c] + cur[i + 1:]
                    if _lineup_ok(new, cap, stack_min, bring_min, exclude):
                        best = (gain, new)
                        break
        if best is None:                                    # two-player swaps
            for i in range(len(cur)):
                for k in range(i + 1, len(cur)):
                    oi, ok_ = cur[i], cur[k]
                    v_out = val[oi["name"]] + val[ok_["name"]]
                    room = cap - cur_sal + oi["salary"] + ok_["salary"]
                    for pa in alt[oi["pos"]]:
                        for pb in alt[ok_["pos"]]:
                            d_rb = (pa == "RB") + (pb == "RB") - (oi["pos"] == "RB") - (ok_["pos"] == "RB")
                            d_wr = (pa == "WR") + (pb == "WR") - (oi["pos"] == "WR") - (ok_["pos"] == "WR")
                            if not shape_ok(n_rb + d_rb, n_wr + d_wr):
                                continue
                            la, lb = by_pos.get(pa, ()), by_pos.get(pb, ())
                            if not la or not lb:
                                continue
                            top_b = val[lb[0]["name"]]
                            for a in la:
                                va = val[a["name"]]
                                if va + top_b - v_out <= 1e-9 or (best and va + top_b - v_out <= best[0]):
                                    break
                                if a["name"] in names or sal[a["name"]] >= room:
                                    continue
                                for b in lb:
                                    gain = va + val[b["name"]] - v_out
                                    if gain <= 1e-9 or (best and gain <= best[0]):
                                        break
                                    if (b["name"] in names or b["name"] == a["name"]
                                            or sal[a["name"]] + sal[b["name"]] > room):
                                        continue
                                    new = [x for j, x in enumerate(cur) if j not in (i, k)] + [a, b]
                                    if _lineup_ok(new, cap, stack_min, bring_min, exclude):
                                        best = (gain, new)
                                        break
        if best is not None:
            cur, cur_v = best[1], cur_v + best[0]
            improved = True
    return cur


def _kick(lu, players, objective, cap, stack_min=0, bring_min=0, exclude=None):
    """Iterated local search on a polished lineup: bar each of its players in
    turn, re-polish without him (_polish over the rest of the pool), then let
    him compete back (a full _polish); keep any lineup that beats the current
    one and go round again. A perturbation plus the two-player polish reaches
    most three-player swaps, which the polish alone cannot make: on a pool
    built to need one (tests: the leverage board), the polish stopped at
    186.21 against an exact 186.67."""
    val = lambda lineup: sum(_value(p, objective) for p in lineup)
    cur, cur_v = list(lu), val(lu)
    improved = True
    while improved:
        improved = False
        for out in list(cur):
            if "_pk" in out:
                continue
            rest = [p for p in players if p["name"] != out["name"]]
            # the barred player's slot is refilled by the best LEGAL fill of the
            # rest of the pool (the drop is allowed: that is the perturbation)
            start = _refill(cur, out, rest, objective, cap, stack_min, bring_min, exclude)
            if start is None:
                continue
            cand = _polish(start, rest, objective, cap, stack_min, bring_min, exclude)
            cand = _polish(cand, players, objective, cap, stack_min, bring_min, exclude)
            v = val(cand)
            if v > cur_v + 1e-9:
                cur, cur_v, improved = cand, v, True
                break
    return cur


def _dst_kick(lu, players, objective, cap, stack_min=0, bring_min=0, exclude=None):
    """The one move no swap neighbourhood crosses: a different DEFENSE, which
    bars every player on the team it faces. For each other defense, the
    lineup's players on that team are replaced by the cheapest legal player of
    the same position, then the whole lineup is re-polished; the best result
    wins. On two-game pools (tests) the polish and kicks stopped short exactly
    here: the optimum held the other defense and five players it unlocked."""
    val = lambda lineup: sum(_value(p, objective) for p in lineup)
    best, best_v = lu, val(lu)              # `lu` itself when nothing beats it
    if any("_pk" in p and p["pos"] == "DST" for p in lu):
        return lu                           # the picker forced this defense
    pk_teams = {(p.get("team") or "").upper() for p in lu if "_pk" in p and p["pos"] != "DST"}
    qb = next(p for p in lu if p["pos"] == "QB")
    cheap = {}
    for p in sorted(players, key=lambda p: p["salary"]):
        cheap.setdefault(p["pos"], []).append(p)
    for d in cheap.get("DST", ()):
        if d["name"] in {p["name"] for p in lu} or not d.get("opp"):
            continue
        bar = d["opp"].upper()
        if (qb.get("team") or "").upper() == bar or (bring_min and (qb.get("opp") or "").upper() == bar):
            continue                        # this defense faces the stack or its bring-back
        if bar in pk_teams:
            continue                        # ... or a man the picker forced
        start = [d if p["pos"] == "DST" else p for p in lu]
        names = {p["name"] for p in start}
        ok = True
        for i, p in enumerate(start):
            if p["pos"] == "DST" or (p.get("team") or "").upper() != bar:
                continue
            sub_ = next((c for c in cheap.get(p["pos"], ()) if c["name"] not in names
                         and (c.get("team") or "").upper() != bar), None)
            if sub_ is None:
                ok = False
                break
            names.discard(p["name"])
            names.add(sub_["name"])
            start[i] = sub_
        if not ok or not _lineup_ok(start, cap, stack_min, bring_min, exclude):
            continue
        cand = _kick(_polish(start, players, objective, cap, stack_min, bring_min, exclude),
                     players, objective, cap, stack_min, bring_min, exclude)
        v = val(cand)
        if v > best_v + 1e-9:
            best, best_v = cand, v
    return best


def _refill(cur, out, pool, objective, cap, stack_min, bring_min, exclude):
    """`cur` with `out` replaced by the best-valued player of `pool` that keeps
    every rule (a drop in value is allowed), or None when no single player
    fits."""
    names = {p["name"] for p in cur}
    i = next(j for j, p in enumerate(cur) if p["name"] == out["name"])
    best = None
    for c in sorted(pool, key=lambda p: -_value(p, objective)):
        if c["name"] in names:
            continue
        new = cur[:i] + [c] + cur[i + 1:]
        if _lineup_ok(new, cap, stack_min, bring_min, exclude):
            best = new
            break
    return best


def optimize(players, cap, objective, stack_min=0, restarts=2000, bring_min=0,
             exclude=None, seed=None, polish_top=None, starts_per_team=1, thorough=True, fixed=None):
    """The best lineup for an additive objective under every _build_one rule:
    random greedy fills (_build_one), the best fill of EVERY stack team
    polished by one- and two-player swaps (_polish), the winner kicked
    (_kick: each player barred in turn and the rest re-polished) and, when
    `thorough`, every other defense tried with the team it faces unlocked
    (_dst_kick). Measured exact: on the 12 real cases of 2026-10-16 (four
    week-6 classic slates x cash, ceiling, leverage) against an integer
    program over the same rules (research-side; the server has no solver),
    and on 180 two-game pools against brute force -- the hardest terrain, where
    a defense bars a quarter of the pool. The fills alone, which is all the
    sheet used to run, came back 3-6% short of the same objective every build.

    Seeded from the pool and the objective, so the same pool gives the same
    lineup on every build (the old search drew on the process's global RNG:
    two builds a minute apart handed the owner different lineups).

    `fixed` is the picker's forced players, marked copies inside `players`:
    every fill seats them (_build_one), one fill takes the cheapest legal
    player at every open slot so a legal start exists whenever the salary
    arithmetic allows one, and the bias they carry keeps polish and kicks from
    swapping them out. This is a heuristic search, measured exact on fixtures,
    never a certificate: the caller checks the answer holds them and words a
    miss "the search found no legal lineup", not "none exists" (`exclude`, the
    portfolio's overlap rule, is a different thing)."""
    import hashlib
    by_pos = _by_pos(players)
    if by_pos is None:
        return None
    if seed is None:
        sig = "|".join(sorted(f"{p['name']}:{p['salary']}:{_value(p, objective):.3f}" for p in players))
        seed = int(hashlib.sha1(f"{objective}|{stack_min}|{bring_min}|{sig}".encode()).hexdigest()[:12], 16)
    rng = random.Random(seed)
    teams = sorted({p.get("team") for p in players if p.get("pos") == "QB" and p.get("team")})
    fq = next((p for p in (fixed or ()) if p["pos"] == "QB"), None)
    if fq is not None and fq.get("team"):
        teams = [fq["team"]]            # a forced quarterback is the stack
    best_by = {}                        # stack team -> {roster: (score, lineup)}

    def keep(r):
        if not r:
            return
        lineup, _sal = r
        if not _lineup_ok(lineup, cap, stack_min, bring_min, exclude):
            return
        score = sum(_value(p, objective) for p in lineup)
        qb_team = next((p.get("team") for p in lineup if p["pos"] == "QB"), None)
        fills = best_by.setdefault(qb_team, {})
        key = frozenset(p["name"] for p in lineup)
        if key not in fills:
            fills[key] = (score, lineup)
            if len(fills) > starts_per_team:
                fills.pop(min(fills, key=lambda k: fills[k][0]))
    asc = {pos: sorted(v, key=lambda p: (p["salary"], p["name"])) for pos, v in by_pos.items()} if fixed else None
    for _ in range(restarts):
        st = rng.choice(teams) if (stack_min and teams) else None
        keep(_build_one(by_pos, cap, objective, stack_team=st, stack_min=stack_min,
                        rng=rng, bring_min=bring_min, fixed=fixed, asc=asc))
    if fixed:
        for st in (teams if stack_min else [None]):
            keep(_build_one(by_pos, cap, objective, stack_team=st, stack_min=stack_min,
                            bring_min=bring_min, fixed=fixed, cheapest=True, asc=asc))
    best = None
    ranked = sorted((f for fills in best_by.values() for f in fills.values()), key=lambda x: -x[0])
    for _score, lineup in (ranked[:polish_top] if polish_top else ranked):
        lu = _polish(lineup, players, objective, cap, stack_min, bring_min, exclude)
        sc = sum(_value(p, objective) for p in lu)
        if best is None or sc > best[0] + 1e-9:
            best = (sc, lu)
    if best is None:
        return None
    lu = _kick(best[1], players, objective, cap, stack_min, bring_min, exclude)
    if not thorough:
        return lu
    return _dst_kick(lu, players, objective, cap, stack_min, bring_min, exclude)


# ---- ownership model --------------------------------------------------------
def _set_ownership(players, n_slots=None):
    """Rough field ownership from projection value (proj per $1k). Chalk = high
    value; used for the leverage objective and the contest field weighting.

    `n_slots` is the roster size the ownership has to add up across -- 9 in
    classic, 6 in showdown. Leaving it at 9 for a showdown slate would inflate
    every number by half."""
    n_slots = len(ROSTER) if n_slots is None else n_slots
    for p in players:
        v = p["proj"] / max(1.0, p["salary"] / 1000.0)
        # WITHIN a position the field sorts near-substitutes mostly by
        # PROJECTION (the best RB against the worst run defense is the obvious
        # play at any salary), with value as a mild tiebreaker -- proj^3.0 x
        # value^0.6, tuned on realistic slate gradients to land the shapes a
        # real GPP sheet shows: clear top RB ~25%, his shadow ~22%, a $2.5k
        # punt TE ~4%, chalk DST ~18%.
        p["_vw"] = max(0.5, p["proj"]) ** 3.0 * max(0.01, v) ** 0.6
    budgets = ({"QB": 1.0, "RB": 2.5, "WR": 3.5, "TE": 1.0, "DST": 1.0}
               if n_slots == len(ROSTER) else None)
    groups = {}
    for p in players:
        pos = p.get("pos") if budgets else None
        groups.setdefault(pos if budgets and pos in budgets else "ALL", []).append(p)
    for pos, grp in groups.items():
        bud = (budgets[pos] if pos != "ALL" else float(n_slots)) * 100.0
        # Cap at 45% and REDISTRIBUTE the overflow to the rest of the position,
        # so two mega-chalk backs don't silently delete the budget the field
        # actually spends on everyone else.
        capped = set()
        for _ in range(3):
            rem = [p for p in grp if id(p) not in capped]
            tot = sum(p["_vw"] for p in rem) or 1.0
            left = bud - 45.0 * len(capped)
            over = False
            for p in rem:
                if left * p["_vw"] / tot > 45.0:
                    capped.add(id(p))
                    over = True
            if not over:
                break
        # Assignment happens OUTSIDE the capping passes so every player gets an
        # "own" even if the passes exhaust before converging (min() backstops).
        rem = [p for p in grp if id(p) not in capped]
        tot = sum(p["_vw"] for p in rem) or 1.0
        left = bud - 45.0 * len(capped)
        for p in rem:
            p["own"] = round(min(45.0, max(0.1, left * p["_vw"] / tot)), 1)
        for p in grp:
            if id(p) in capped:
                p["own"] = 45.0


# ---- contest sim (win% / cash% / ROI, any field size) ----------------------
def _field_lineup(by_pos, cap, own_w, rng, tries=8):
    for _ in range(tries):
        used, names, sal, ok = set(), [], 0, True
        fqb_team = None
        for pos in sorted(ROSTER, key=lambda p: len(by_pos[p])):
            pool = [p for p in by_pos[pos] if p["name"] not in used and sal + p["salary"] <= cap]
            if not pool:
                ok = False
                break
            w = [own_w.get(p["name"], 1.0) for p in pool]
            # Real GPP fields stack: roughly two-thirds of tournament lineups
            # pair the QB with his catchers. A field of independent picks is
            # thinner-tailed than the real one, which flattered our win%%.
            if fqb_team and pos in ("WR", "TE", "FLEX") and rng.random() < 0.65:
                w = [wt * (4.0 if p.get("team") == fqb_team and p["pos"] in ("WR", "TE") else 1.0)
                     for wt, p in zip(w, pool)]
            pick = rng.choices(pool, weights=w)[0]
            if pick["pos"] == "QB":
                fqb_team = pick.get("team")
            used.add(pick["name"]); names.append(pick["name"]); sal += pick["salary"]
        if ok and len(names) == len(ROSTER):
            return names
    return None


def payout_table(tiers, contest_size=None):
    """(payout(rank), places_paid, first, pool) from DraftKings' own payout
    table (dk.contest_detail's `payouts`: [{from, to, prize}]), or None when
    the table is missing or malformed. The builder used to price every GPP
    off _gpp_curve -- 20% of the field paid on a fitted power law -- whatever
    the contest really paid: the week-6 $2.75M Millionaire pays 37,425 of
    161,764 (23.1%), so the card's cash line sat 5,072 places short."""
    import bisect
    rows = []
    for t in tiers or []:
        try:
            lo, hi, pr = int(t["from"]), int(t["to"]), float(t["prize"])
        except (KeyError, TypeError, ValueError):
            return None
        if lo < 1 or hi < lo or pr < 0:
            return None
        if pr > 0:
            rows.append((lo, hi, pr))
    if not rows:
        return None
    rows.sort()
    for (a_lo, a_hi, _a), (b_lo, _b_hi, _b) in zip(rows, rows[1:]):
        if b_lo <= a_hi:
            return None                       # overlapping tiers: not a table we can trust
    places = rows[-1][1]
    if contest_size and places > int(contest_size):
        return None
    starts = [r[0] for r in rows]

    def payout(r):
        i = bisect.bisect_right(starts, int(r)) - 1
        if i < 0:
            return 0.0
        lo, hi, pr = rows[i]
        return pr if lo <= int(r) <= hi else 0.0
    pool = sum((hi - lo + 1) * pr for lo, hi, pr in rows)
    return payout, places, payout(1), pool


# A finishing line is READ off the sampled field only when at least this many
# sampled lineups sit above it; past that the number is the fitted normal
# curve's thin tail, not the field. A round floor, set before any measurement.
# On the week-6 $2.75M Millionaire (161,764 entries) a 600-lineup sample reads
# down to rank 2,696: cash (37,425 paid) is readable, first place is 0.004 of a
# sampled lineup and is not -- and the card printed "win ~1 in 374, ROI
# +17,759.8%" off it, the same unresolvable first-place term the September
# audit took off the tournament board's headline (dfs_final_audit_report.md N).
_READ_MIN = 10
# ROI is readable when no more than this share of the prize money is paid
# ABOVE the deepest line the sample reads (a double-up: none of it).
_ROI_UNREAD_MAX = 0.10


def resolution(sample, C, places, payout, pool):
    """What a `sample`-lineup field can say about a C-entry contest:
    {sample, entries, per_sample, best_rank_read, win, top1, cash, roi,
    prize_above_share, note}. Each flag is True when that figure is read off
    the field rather than off the normal curve's tail."""
    sample = max(1, int(sample or 0))
    C = max(2, int(C))
    r_read = _READ_MIN * C / float(sample)      # the best rank with >= _READ_MIN sampled lineups above it
    k = min(int(places), int(math.ceil(r_read)) - 1)
    above = sum(payout(r) for r in range(1, k + 1)) if k >= 1 else 0.0
    share = above / pool if pool else 0.0
    out = {"sample": sample, "entries": C, "per_sample": round(C / float(sample), 1),
           "best_rank_read": max(1, int(math.ceil(r_read))),
           "win": r_read <= 1.0, "top1": r_read <= 0.01 * C, "cash": r_read <= places,
           "roi": share <= _ROI_UNREAD_MAX, "prize_above_share": round(share, 3)}
    if not out["roi"] or not out["win"]:
        out["note"] = (f"{sample:,} sampled lineups against {C:,} entries: one sampled lineup stands for "
                       f"{C / float(sample):,.0f} real ones, so the field is read down to rank "
                       f"{out['best_rank_read']:,} and no higher. "
                       + ("First place cannot be read off it" if not out["win"] else "")
                       + (f"{' and ' if not out['win'] else ''}{100 * share:.0f}% of the prize money is paid "
                          f"above that line, so the ROI cannot either" if not out["roi"] else "")
                       + ". For this contest the PC's tournament board (a 300,000-lineup field) is the tool.")
    return out


def _payout_setup(contest, C, prize_pool, first_prize, entry_fee, payouts):
    """(payout, places, first, pool, each) for a C-entry contest: DraftKings'
    own table when the build carries it, else the double-up rule or the
    fitted GPP curve. `each` is a double-up's flat prize, else None."""
    tab = payout_table(payouts, C) if payouts else None
    if tab:
        payout, places, first, pool = tab
        return payout, places, first, pool, None
    pool = float(prize_pool) if prize_pool else entry_fee * C * 0.85
    if contest == "double_up":
        places = max(1, int(round(0.45 * C)))
        each = pool / places
        return (lambda r: each if r <= places else 0.0), places, each, pool, each
    first = float(first_prize) if first_prize else 0.20 * pool
    payout, places = _gpp_curve(C, pool, first, entry_fee)
    return payout, places, first, pool, None


def contest_sim(your_lineup, players, contest="gpp", entry_fee=1.0, contest_size=None,
                prize_pool=None, first_prize=None, sample_size=500, n_iter=400,
                extra_lineups=None, seed=None, payouts=None, field_players=None, ref=None):
    """`field_players` (a picked build) is the pool the FIELD is drawn from --
    the public still plays the man the owner excluded and never the man he
    forced past the depth gate -- while our lineups read their arrays from
    `players`; `ref` pins the field's floor to the UNPICKED optimum, so a picked
    card meets the unpicked card's field (the floor keys on the lineup handed
    in, and a weaker forced lineup would lower it). Both None: as before."""
    fp = players if field_players is None else field_players
    by_pos = _by_pos(fp)
    arr = {p["name"]: p.get("arr") for p in fp if p.get("arr")}
    if field_players is not None:
        for p in players:
            if p.get("arr"):
                arr.setdefault(p["name"], p["arr"])
    if by_pos is None or not arr:
        return None
    L = min(len(a) for a in arr.values())
    own_w = {p["name"]: max(0.1, p.get("own", 8.0)) for p in fp}
    proj_of = {p["name"]: p.get("proj", 0.0) for p in fp}
    if field_players is not None:
        for p in players:
            proj_of.setdefault(p["name"], p.get("proj", 0.0))
    # a fixed field for every build; `seed` draws another (the sample-noise
    # measurement, dfs_backtest.run_sample)
    rng = random.Random(12345 if seed is None else seed)
    ref = sum(proj_of.get(p["name"], 0.0) for p in your_lineup) if ref is None else float(ref)
    floor_q = 0.84 * ref
    field, attempts = [], 0
    while len(field) < sample_size and attempts < sample_size * 40:
        attempts += 1
        fl = _field_lineup(by_pos, CAP, own_w, rng)
        if fl and sum(proj_of.get(nm, 0.0) for nm in fl) >= floor_q:
            field.append(fl)
    if len(field) < 30:
        return None

    C = max(2, int(contest_size or (len(field) + 1)))
    payout, places, first, pool, each = _payout_setup(contest, C, prize_pool, first_prize,
                                                      entry_fee, payouts)
    grid = _rank_grid(places)

    import statistics
    def score(names, it):
        return sum((arr.get(nm) or [0])[it % L] for nm in names)

    # One sampled field, every portfolio lineup scored against it -- cheaper
    # than a field per lineup and statistically consistent across the set.
    all_lineups = [your_lineup] + list(extra_lineups or [])
    name_sets = [[p["name"] for p in lu] for lu in all_lineups]
    K = len(all_lineups)
    win = [0.0] * K; cash = [0.0] * K; ret = [0.0] * K; top1 = [0.0] * K
    top1_line = max(1, int(0.01 * C))
    for it in range(n_iter):
        fs = [score(f, it) for f in field]
        mu = statistics.fmean(fs); sd = statistics.pstdev(fs) or 1.0
        for k, ynames in enumerate(name_sets):
            ys = score(ynames, it)
            q = max(1e-12, min(1.0, 1.0 - _ncdf((ys - mu) / sd)))
            winp = math.exp((C - 1) * math.log(1.0 - q)) if q < 1.0 else 0.0
            win[k] += winp
            mr = 1.0 + (C - 1) * q
            sr = math.sqrt(max(1e-9, (C - 1) * q * (1.0 - q)))
            cash[k] += _ncdf((places - mr) / sr)
            top1[k] += _ncdf((top1_line - mr) / sr)
            if each is not None:
                ret[k] += each * _ncdf((places - mr) / sr)
            else:
                ev = first * winp
                for r, wd in grid:
                    ev += payout(r) * _npdf((r - mr) / sr) / sr * wd
                ret[k] += ev
    per = [{"win_pct": round(100 * win[k] / n_iter, 4),
            "cash_pct": round(100 * cash[k] / n_iter, 1),
            "top1_pct": round(100 * top1[k] / n_iter, 2),
            "roi_pct": round(100 * (ret[k] / n_iter - entry_fee) / entry_fee, 1)}
           for k in range(K)]
    out = dict(per[0])
    out.update({"avg_return": round(ret[0] / n_iter, 2), "sample_size": len(field),
                "field_hash": simulate._field_hash(field),
                "entries": C, "contest": contest, "entry_fee": entry_fee,
                "prize_pool": round(pool), "first_prize": round(first),
                "places_paid": places, "payouts_from": "draftkings" if payouts and payout_table(payouts, C) else "model",
                "resolved": resolution(len(field), C, places, payout, pool)})
    if K > 1:
        out["lineups"] = per
    return out


# ---- public build -----------------------------------------------------------
def _pct(a, q):
    s = sorted(a)
    return s[min(len(s) - 1, int(q * len(s)))]


def _special_arr(pos, preseason, n=1500):
    """Measured exhibition scoring for a KICKER, or None.

    Defenses used to come through here too and must not any more: the pool now
    builds them from the opposing offense's per-iteration output, which is the
    only way the two defenses in a game end up correlated. Drawing them here
    would replace that with an independent sample and put the original problem
    straight back -- rostering both defenses would look like diversification
    again.

    These two are the only units that play the whole game, and in preseason they
    are the highest-scoring positions on the board -- but neither was modelled
    for August. Kickers are not in the game sim at all, so they fell through to
    DK's AvgPointsPerGame, which is a REGULAR-SEASON average, plus an invented
    Gaussian. And dst_projections asks Sleeper for season_type=regular
    unconditionally, so a preseason DST carried a regular-season projection.
    Both now come from nfl_preseason's measured decile ladder."""
    if not preseason or (pos or "").upper() != "K":
        return None
    try:
        import nfl_preseason
    except Exception:
        return None
    return nfl_preseason.special_samples("K", n, random)


def _sd_field_lineup(players, cap, own_w, rng, tries=10):
    """One plausible opponent showdown roster: a captain drawn by ownership, then
    five FLEX the same way."""
    for _ in range(tries):
        cap_p = rng.choices(players, weights=[own_w.get(p["name"], 1.0) for p in players])[0]
        budget = cap - cap_p["cpt_salary"]
        if budget < 0:
            continue
        picked, sal, teams, used = [], 0, {cap_p["team"]}, {cap_p["name"]}
        for slot in range(5):
            pool = [p for p in players
                    if p["name"] not in used and sal + p["salary"] <= budget]
            if slot == 4 and len(teams) < SHOWDOWN_MIN_TEAMS:
                pool = [p for p in pool if p["team"] and p["team"] not in teams]
            if not pool:
                break
            pick = rng.choices(pool, weights=[own_w.get(p["name"], 1.0) for p in pool])[0]
            used.add(pick["name"]); picked.append(pick); sal += pick["salary"]
            if pick["team"]:
                teams.add(pick["team"])
        if len(picked) == 5 and len(teams) >= SHOWDOWN_MIN_TEAMS:
            return [("CPT", cap_p["name"])] + [("FLEX", p["name"]) for p in picked]
    return None


def _showdown_contest_sim(your, players, contest, entry_fee, contest_size,
                          prize_pool, first_prize, sample_size=400, n_iter=300, payouts=None):
    """One lineup against a simulated field (see _showdown_contest_sims)."""
    got = _showdown_contest_sims([your], players, contest, entry_fee, contest_size,
                                 prize_pool, first_prize, sample_size=sample_size,
                                 n_iter=n_iter, payouts=payouts)
    return got[0] if got else None


def _showdown_contest_sims(yours, players, contest, entry_fee, contest_size,
                           prize_pool, first_prize, sample_size=400, n_iter=300, payouts=None,
                           field_players=None, ref=None):
    """Same maths as the classic contest sim, over showdown rosters, for
    SEVERAL lineups against ONE field: the field's per-iteration score
    distribution is the expensive part (sample_size lineups x n_iter), and it
    is the same field whichever of our entries is being scored, so a
    five-captain multi-entry costs about what one lineup did. The captain's
    1.5x has to be applied per SLOT, not per player -- the same name scores
    differently depending on where the field put him. Returns one result per
    lineup (None where nothing could be scored). `field_players` and `ref`
    as contest_sim's (a picked build)."""
    import statistics
    fp = players if field_players is None else field_players
    arr = {p["name"]: p.get("arr") for p in fp if p.get("arr")}
    if field_players is not None:
        for p in players:
            if p.get("arr"):
                arr.setdefault(p["name"], p["arr"])
    if not arr or not yours:
        return [None for _ in yours]
    L = min(len(a) for a in arr.values())
    own_w = {p["name"]: max(0.1, p.get("own", 8.0)) for p in fp}
    proj_of = {p["name"]: p.get("proj", 0.0) for p in fp}
    if field_players is not None:
        for p in players:
            proj_of.setdefault(p["name"], p.get("proj", 0.0))
    rng = random.Random(12345)

    def sc(slots, it):
        return sum((CPT_MULT if s == "CPT" else 1.0) * (arr.get(nm) or [0])[it % L]
                   for s, nm in slots)

    # The field is trimmed to lineups projecting near OUR level (the bottom of
    # a real field never threatens a top-1% finish); the bar is set off the
    # strongest of our entries so every entry meets the same opponents.
    if ref is None:
        ref = max(sum((CPT_MULT if s == "CPT" else 1.0) * proj_of.get(nm, 0.0) for s, nm in y)
                  for y in yours)
    floor_q = 0.84 * ref
    field, attempts = [], 0
    while len(field) < sample_size and attempts < sample_size * 40:
        attempts += 1
        fl = _sd_field_lineup(fp, CAP, own_w, rng)
        if fl and sum((CPT_MULT if s == "CPT" else 1.0) * proj_of.get(nm, 0.0)
                      for s, nm in fl) >= floor_q:
            field.append(fl)
    if len(field) < 30:
        return [None for _ in yours]
    C = max(2, int(contest_size or (len(field) + 1)))
    payout, places, first, pool, each = _payout_setup(contest, C, prize_pool, first_prize,
                                                      entry_fee, payouts)
    grid = _rank_grid(places)
    top1_line = max(1, int(0.01 * C))
    res = resolution(len(field), C, places, payout, pool)
    src = "draftkings" if payouts and payout_table(payouts, C) else "model"
    acc = [[0.0, 0.0, 0.0, 0.0] for _ in yours]        # win, cash, ret, top1
    for it in range(n_iter):
        fs = [sc(f, it) for f in field]
        mu = statistics.fmean(fs); sd = statistics.pstdev(fs) or 1.0
        for k, your in enumerate(yours):
            ys = sc(your, it)
            q = max(1e-12, min(1.0, 1.0 - _ncdf((ys - mu) / sd)))
            winp = math.exp((C - 1) * math.log(1.0 - q)) if q < 1.0 else 0.0
            mr = 1.0 + (C - 1) * q
            sr = math.sqrt(max(1e-9, (C - 1) * q * (1.0 - q)))
            a = acc[k]
            a[0] += winp
            a[1] += _ncdf((places - mr) / sr)
            a[3] += _ncdf((top1_line - mr) / sr)
            if each is not None:
                a[2] += each * _ncdf((places - mr) / sr)
            else:
                ev = first * winp
                for r, wd in grid:
                    ev += payout(r) * _npdf((r - mr) / sr) / sr * wd
                a[2] += ev
    out = []
    for win, cash, ret, top1 in acc:
        ret /= n_iter
        out.append({"win_pct": round(100 * win / n_iter, 4), "cash_pct": round(100 * cash / n_iter, 1),
                    "top1_pct": round(100 * top1 / n_iter, 2),
                    "roi_pct": round(100 * (ret - entry_fee) / entry_fee, 1),
                    "avg_return": round(ret, 2), "sample_size": len(field), "entries": C,
                    "contest": contest, "entry_fee": entry_fee, "prize_pool": round(pool),
                    "first_prize": round(first), "places_paid": places,
                    "payouts_from": src, "resolved": res})
    return out


# A showdown is one game: simulate that game deep instead of sixteen games
# shallow. 12,000 draws is what the contest sim's tail needs (a 132k-entry
# field is decided past the 99th percentile) at a few seconds on one core;
# the classic pool stays at its 3,000 across the week.
_SD_SIMS = 12000
# Contest-sim iterations for a showdown build. The field's per-iteration score
# distribution is shared by every entry (_showdown_contest_sims), so this is
# paid once per build, not per entry.
_SD_ITERS = 800
_SD_MAX_ENTRIES = 8
_SD_RULES = (
    "no defense beside the opposing team's captain",
    "no kicker or defense as a GPP captain",
    "at most two kickers/defenses and two $2,000-or-under punts",
    "one tight end per team",
    "a punt must hold a role: RB1-2, WR1-3 or TE1 on the depth chart",
    "roster gate: QB1, RB1-2, WR1-3, TE1-2 by Sleeper's depth chart; OUT/IR/doubtful out",
)


def _build_showdown(csv_players, week, objective, contest, contest_size,
                    entry_fee, prize_pool, first_prize, preseason, field_size=None,
                    n_lineups=1, lock_s=None, payouts=None, pk=None):
    ents = showdown_pool(csv_players)
    if len(ents) < len(SHOWDOWN_ROSTER):
        return {"error": f"showdown needs {len(SHOWDOWN_ROSTER)} available players "
                         f"(got {len(ents)} after dropping OUT/IR)"}
    teams = {e.get("team") for e in ents if e.get("team")}
    # Showdown serves the discrete scorer on BOTH of its surfaces. Leaving the
    # sheet on the old one would have the builder and the tournament board
    # disagree about the same game, which is worse than either choice: the
    # lineups this emits would be scored on a support the board says is illegal.
    # Classic is untouched -- nfl_dfs_sim.SD_DISCRETE is read here and in
    # dfs_tourney.build_nfl_showdown, nowhere else.
    pool = nfl_dfs_sim.player_pool(week, n=_SD_SIMS, preseason=preseason,
                                   teams=teams,
                                   discrete=nfl_dfs_sim.SD_DISCRETE) or {}
    _nidx, _norm = _norm_index(pool)
    _deep = _deep_fallback(pool, preseason)
    unmatched, excluded = [], []
    for e in ents:
        sp = _special_arr(e["pos"], preseason)
        sim = _pool_match(pool, e["name"], e["pos"], e.get("team"), _nidx, _norm)
        if sp:                                  # kicker / defense in August
            e["arr"] = sp
            e["proj"] = sum(sp) / len(sp)
            e["ceiling"], e["floor"] = round(_pct(sp, 0.9), 1), round(_pct(sp, 0.1), 1)
        elif sim and sim.get("arr"):
            e["proj"], e["ceiling"], e["floor"], e["arr"] = (
                sim["proj"], sim["ceiling"], sim["floor"], sim["arr"])
        else:
            if not preseason:
                # No projection this week is not a bargain at $3,000.
                excluded.append({"name": e["name"], "pos": e["pos"],
                                 "team": e.get("team"),
                                 "why": "no Sleeper projection this week"})
                e["_drop"] = True
                unmatched.append(e["name"])
                continue
            pr = _deep.get(e["pos"], e["proj"])
            e["proj"] = pr
            e["arr"] = [max(0.0, random.gauss(pr, pr * 0.5 + 2)) for _ in range(1500)]
            e["ceiling"], e["floor"] = round(_pct(e["arr"], 0.9), 1), round(_pct(e["arr"], 0.1), 1)
            unmatched.append(e["name"])
        e["proj"] = round(e["proj"], 1)
    ents = [e for e in ents if not e.get("_drop")]
    if pk:
        miss = sorted(set(pk.force) - {e["name"] for e in ents})
        if miss:
            return {"error": f"player picker: no Sleeper projection this week for {', '.join(miss)}, so he "
                             f"cannot be forced", "pick_refused": True}
        pre_gate = {e["name"]: e for e in ents}
    try:
        ents, _dx = _apply_depth(ents, preseason,
                                 lock_s if lock_s is not None else _lock_seconds(None, csv_players, contest))
    except RosterUnavailable as _e:
        return {"error": f"no trustworthy roster for the depth-chart gate; the sheet is not built with the "
                         f"gate off ({_e})", "roster_source": _e.state, "excluded": excluded[:40]}
    excluded += _dx
    restored, waived = [], []
    if pk:
        restored, why = _pk_restore(pk, ents, _dx, pre_gate, waived)
        if why:
            return {"error": f"player picker: {why}", "pick_refused": True}
    if len(ents) < len(SHOWDOWN_ROSTER):
        return {"error": f"showdown needs {len(SHOWDOWN_ROSTER)} rostered, projected "
                         f"players (got {len(ents)} after the depth chart)",
                "excluded": excluded[:40]}
    _set_ownership(ents, n_slots=len(SHOWDOWN_ROSTER))
    n_entries = max(1, min(_SD_MAX_ENTRIES, int(n_lineups or 1)))
    # the picker: our pool is the field's minus the excluded, the forced as
    # marked copies taken after the ownership (the field keeps `ents`); the
    # unpicked builds set the field's bar, so a picked card meets the
    # unpicked card's field
    ours, ref, unpicked_best = ents, None, None
    if pk:
        fixed = [dict(e, _pk=True) for e in ents + restored if e["name"] in pk.force]
        ours = [e for e in ents if e["name"] not in pk.exclude and e["name"] not in pk.force] + fixed
        ub = optimize_showdown_multi(ents, CAP, objective, n_entries)
        if ub:
            ref = max(CPT_MULT * c["proj"] + sum(q["proj"] for q in pk_) for c, pk_, _s in ub)
            unpicked_best = round(CPT_MULT * ub[0][0]["proj"] + sum(q["proj"] for q in ub[0][1]), 1)
    builds = optimize_showdown_multi(ours, CAP, objective, n_entries, pick=pk)
    if pk:
        builds = [b for b in builds if pk.force <= {b[0]["name"]} | {q["name"] for q in b[1]}
                  and (not pk.captain or b[0]["name"] == pk.captain)]
        if not builds:
            return {"error": f"player picker: the search found no legal showdown lineup {_pk_what(pk)}"
                             + (f" with {pk.captain} at captain" if pk.captain else "") + " under the cap",
                    "pick_refused": True}
        waived += _sd_pick_waivers(pk, builds, ours, objective)
    if not builds:
        return {"error": "no valid showdown lineup under the cap"}
    all_slots = [[("CPT", c["name"])] + [("FLEX", p["name"]) for p in pk] for c, pk, _s in builds]
    csims = [None for _ in builds]
    if contest:
        try:
            csims = _showdown_contest_sims(all_slots, ours, contest, entry_fee, contest_size,
                                           prize_pool, first_prize,
                                           sample_size=max(150, min(2000, int(field_size or 400))),
                                           n_iter=_SD_ITERS, payouts=payouts,
                                           field_players=(ents if pk else None), ref=ref)
        except Exception as _e:
            errlog.note("NFLDFS-sd-contest", _e)
            csims = [None for _ in builds]
    entries = []
    for (cap_p, picked, sal), csim in zip(builds, csims):
        lineup = [cap_p] + picked
        L = min(len(p["arr"]) for p in lineup)
        totals = [CPT_MULT * cap_p["arr"][i] + sum(p["arr"][i] for p in picked)
                  for i in range(L)]
        rows = []
        for slot, p in [("CPT", cap_p)] + [("FLEX", q) for q in picked]:
            mult = CPT_MULT if slot == "CPT" else 1.0
            rows.append({"slot": slot, "name": p["name"], "pos": p["pos"], "team": p["team"],
                         "depth": p.get("depth"),
                         "salary": p["cpt_salary"] if slot == "CPT" else p["salary"],
                         "proj": round(mult * p["proj"], 1),
                         "ceiling": round(mult * p["ceiling"], 1),
                         "floor": round(mult * p["floor"], 1), "own": p.get("own")})
        entries.append({"captain": cap_p["name"], "captain_pos": cap_p["pos"],
                        "captain_team": cap_p["team"],
                        "lineup": rows, "salary": sal, "salary_left": CAP - sal,
                        "proj": round(sum(r["proj"] for r in rows), 1),
                        "floor": round(_pct(totals, 0.10), 1),
                        "median": round(_pct(totals, 0.50), 1),
                        "ceiling": round(_pct(totals, 0.90), 1),
                        "max": round(max(totals), 1),
                        "teams": sorted({p["team"] for p in lineup if p["team"]}),
                        "contest_sim": csim})
    # With a contest to score against, the entries rank by their chance of a
    # top-1% finish in it; without one, by the objective's own joint score.
    if any(e.get("contest_sim") for e in entries):
        order = sorted(range(len(entries)),
                       key=lambda i: -((entries[i]["contest_sim"] or {}).get("top1_pct") or 0.0))
        entries = [entries[i] for i in order]
        builds = [builds[i] for i in order]
        csims = [csims[i] for i in order]
    # Entry 1 is the build; the top-level fields carry it so every reader of
    # a single-lineup result (the look-back, the card) is unchanged.
    cap_p, picked, sal = builds[0]
    lineup = [cap_p] + picked
    L = min(len(p["arr"]) for p in lineup)
    totals = [CPT_MULT * cap_p["arr"][i] + sum(p["arr"][i] for p in picked)
              for i in range(L)]
    csim = csims[0]
    rows = entries[0]["lineup"]
    teams = entries[0]["teams"]
    # Flat pricing changes what the optimizer is solving. With one salary for the
    # whole pool the knapsack dissolves: there is no value play, no salary saver,
    # nothing to punt, and points-per-dollar is a constant. Every lineup that
    # fills six slots costs the same, so the only questions left are who plays
    # and who else will roster them. Worth saying out loud rather than leaving
    # the reader to notice the salary column never changes.
    _sals = {e["salary"] for e in ents}
    flat = len(_sals) == 1
    _sk = _status_seen(csv_players)
    return {"mode": "showdown", "roster": SHOWDOWN_ROSTER,
            "status_known": _sk,
            "status_warning": None if _sk else
            ("DK's Status column isn't in this paste, so OUT and IR players "
             "could NOT be excluded - check every name is active before you "
             "enter. Copy the whole CSV including its header row."),
            "flat_priced": flat,
            "flat_note": ("Every player on this slate is priced the same "
                          f"(${sorted(_sals)[0]:,} FLEX). Points per dollar is a "
                          "constant, so the cap can't distinguish lineups - the "
                          "whole decision is playing time and ownership.")
            if flat else None,
            "week": week, "objective": objective, "stack": False,
            "salary": sal, "cap": CAP, "salary_left": CAP - sal,
            "proj": round(sum(r["proj"] for r in rows), 1),
            "floor": round(_pct(totals, 0.10), 1),
            "median": round(_pct(totals, 0.50), 1),
            "ceiling": round(_pct(totals, 0.90), 1),
            "max": round(max(totals), 1),
            "lineup": rows, "contest_sim": csim,
            "entries": entries, "n_entries": len(entries),
            "rules": list(_SD_RULES), "n_sims": _SD_SIMS,
            # which model made these worlds, so a sheet can be audited for its
            # simulator the way a tournament board already can
            "simulator": nfl_dfs_sim.sim_stamp(n=_SD_SIMS, preseason=preseason,
                                               discrete=nfl_dfs_sim.SD_DISCRETE),
            # what the weekly lines' adjustments DID on this build (recent usage, the
            # WR/RB matchup hook), beside the rule above
            "inputs": nfl_dfs_sim.week_stamp(week, preseason),
            "unmatched": unmatched[:20], "n_pool": len(ents),
            "excluded": excluded[:40], "n_excluded": len(excluded),
            "roster_source": roster_state(),
            "teams": teams,
            **({"pick": pick_stamp(pk, waived), "unpicked_best": unpicked_best} if pk else {}),
            "note": "Showdown Captain Mode: 1 CPT at 1.5x points and 1.5x salary, "
                    "plus 5 FLEX from any position, spanning both teams. Players DK "
                    "lists OUT or IR are excluded."}


def build(csv_text, week=None, objective="projection", stack=True, contest=None,
          contest_size=None, entry_fee=1.0, prize_pool=None, first_prize=None,
          preseason=False, mode="auto", field_size=None, n_lineups=1, uniq=2,
          contest_probe=None, starts=None, payouts=None, pick=None):
    """`week` None means "the slate's own week": read off the export's game
    dates, else the live slate's first kickoff (`starts`), else the calendar's
    current week. A number is the owner's explicit choice and is used only
    when the slate itself carries no date. `pick` is the player picker's
    parsed pick (pick_parse), or None."""
    csv_players = simulate.parse_dk_csv(csv_text)
    if not csv_players:
        return {"error": "couldn't read any players out of that CSV"}
    # The slate's own dates outrank the calendar: a Week 1 CSV in August is a
    # REGULAR-season contest and must not run the inverted preseason model.
    _sp, _sw = slate_season_type(csv_players, starts=starts)
    _src = "your CSV's game dates" if _slate_dates(csv_players) else "the DraftKings slate's kickoff"
    if _sp is not None:
        preseason = _sp
        if _sw is not None:
            week = _sw
        slate_note = (f"preseason (read from {_src})" if _sp else
                      f"Week {week} regular season (read from {_src})")
    elif week:
        slate_note = (("preseason" if preseason else f"Week {week} regular season")
                      + " (from the toggle - this CSV carries no game dates)")
    else:
        import nfl_game_sim
        week = nfl_game_sim.current_week(preseason)
        slate_note = (("preseason" if preseason else f"Week {week} regular season")
                      + " (this week, from the calendar - the slate carries no date)")
    _lock_s = _lock_seconds(starts, csv_players, contest)
    detected = detect_mode(csv_players)
    use = detected if mode in (None, "", "auto") else mode
    pk = None
    if pick:
        pk, why = pick_resolve(pick, csv_players, use, stack=bool(stack))
        if why:
            return {"error": f"player picker: {why}", "pick_refused": True}
    if use == "showdown":
        res = _build_showdown(csv_players, week, objective, contest, contest_size,
                              entry_fee, prize_pool, first_prize, preseason,
                              field_size=field_size, n_lineups=n_lineups, lock_s=_lock_s,
                              payouts=payouts, pk=pk)
        if isinstance(res, dict) and "error" not in res:
            res["slate_mode"] = slate_note
        return res
    if len(csv_players) < len(ROSTER):
        return {"error": f"need at least {len(ROSTER)} players in the CSV (got {len(csv_players)})"}
    csv_players = [c for c in csv_players if _playable(c)]
    pool = nfl_dfs_sim.player_pool(week, preseason=preseason)
    if not pool:
        return {"error": "projections not ready - the weekly sim is still building, retry shortly"}

    # Both indexes, same as the showdown path. Building them only there left the
    # classic path calling _pool_match with names it had never defined -- a
    # NameError on EVERY classic build, which is the format every multi-game
    # preseason slate uses. Showdown is the exception, not the rule.
    _nidx, _norm = _norm_index(pool)
    _deep = _deep_fallback(pool, preseason)
    players, unmatched, excluded = [], [], []
    for c in csv_players:
        nm = c["name"]
        c["opp"] = _opp_of(c.get("team"), c.get("game"))
        pos = (c.get("pos") or "").upper().split("/")[0]
        elig = _elig(pos)
        if not elig:
            continue
        sim = _pool_match(pool, nm, pos, c.get("team"), _nidx, _norm)
        _sp = _special_arr(pos, preseason)
        if _sp:                                     # kicker / defense in August
            samp = _sp
            proj = sum(_sp) / len(_sp)
            ceiling, floor = round(_pct(_sp, 0.9), 1), round(_pct(_sp, 0.1), 1)
        elif sim and sim.get("arr"):
            proj, ceiling, floor, samp = sim["proj"], sim["ceiling"], sim["floor"], sim["arr"]
        else:                                       # in the CSV but not projected
            if not preseason:
                # The soft fallback handed an unprojected player DK's season
                # average -- a practice-squad receiver at $3,000 came back the
                # best value on the board. In season an unknown is left out.
                excluded.append({"name": nm, "pos": pos, "team": c.get("team"),
                                 "why": "no Sleeper projection this week"})
                unmatched.append(nm)
                continue
            proj = _deep.get(pos, c.get("proj") or 0.0)
            samp = [max(0.0, random.gauss(proj, proj * 0.5 + 2)) for _ in range(1500)]
            ceiling, floor = round(_pct(samp, 0.9), 1), round(_pct(samp, 0.1), 1)
            unmatched.append(nm)
        players.append({"name": nm, "pos": pos, "team": c.get("team"), "opp": c.get("opp"),
                        "salary": int(c["salary"]), "dk_status": (c.get("status") or "").upper(),
                        "proj": round(proj, 1), "ceiling": ceiling, "floor": floor,
                        "elig": elig, "arr": samp})
    if pk:
        miss = sorted(set(pk.force) - {p["name"] for p in players})
        if miss:
            return {"error": f"player picker: no Sleeper projection this week for {', '.join(miss)}, so he "
                             f"cannot be forced", "pick_refused": True}
        pre_gate = {p["name"]: p for p in players}
    try:
        players, _dx = _apply_depth(players, preseason, _lock_s)
    except RosterUnavailable as _e:
        return {"error": f"no trustworthy roster for the depth-chart gate; the sheet is not built with the "
                         f"gate off ({_e})", "roster_source": _e.state, "excluded": excluded[:40]}
    excluded += _dx
    restored, waived, pk_notes = [], [], []
    if pk:
        restored, why = _pk_restore(pk, players, _dx, pre_gate, waived)
        if why:
            return {"error": f"player picker: {why}", "pick_refused": True}
    if _by_pos(players) is None:
        return {"error": "the slate doesn't cover every roster slot with rostered, "
                         "projected players (need QB/RB/WR/TE/DST)",
                "excluded": excluded[:40]}

    _set_ownership(players)
    # Stacks are the whole point of NFL DFS -- a QB and his pass-catchers boom
    # together -- and they used to switch on only for the GPP objectives, so a
    # default build came back stackless. Every objective stacks now; the
    # top-heavy ones stack deeper.
    try:
        import dfslog                      # look-back correction in force (gated)
        dfslog.adjust("nfl", players + restored)
    except Exception as _e:
        errlog.note("DFSLB-adjust", _e, path="nfl")
    stack_min = (2 if objective in ("ceiling", "leverage") else 1) if stack else 0
    # The GPP shapes also bring back one catcher from the QB's opponent: a
    # shootout lifts both sides, and the game-stack owns that outcome.
    bring_min = 1 if (stack and objective in ("ceiling", "leverage")) else 0
    # The picker's pool: the field's minus the excluded, the forced as marked
    # COPIES taken only now -- after the ownership and the look-back
    # correction, which rewrites proj and replaces arr (a copy taken before
    # it would choose, print and score three different books) -- plus any
    # man forced past the depth gate. The field keeps `players`.
    ours, fixed, unpicked_proj = players, None, None
    if pk:
        fixed = [dict(p, _pk=True) for p in players + restored if p["name"] in pk.force]
        ours = [p for p in players if p["name"] not in pk.exclude and p["name"] not in pk.force] + fixed
        u = optimize(players, CAP, objective, stack_min=stack_min, bring_min=bring_min)
        if not u and bring_min:
            u = optimize(players, CAP, objective, stack_min=stack_min)
        unpicked_proj = sum(p["proj"] for p in u) if u else None
    lineup = optimize(ours, CAP, objective, stack_min=stack_min, bring_min=bring_min, fixed=fixed)
    if not lineup and bring_min:
        lineup = optimize(ours, CAP, objective, stack_min=stack_min, fixed=fixed)
        if lineup and pk:
            waived.append("the bring-back: the search found no lineup holding your picks with a receiver from "
                          "the quarterback's opponent")
    if pk and (not lineup or not pk.force <= {p["name"] for p in lineup}):
        return {"error": f"player picker: the search found no legal lineup {_pk_what(pk)} under the cap, the "
                         f"stack and the defense rules",
                "pick_refused": True}
    if not lineup:
        return {"error": "no valid lineup under the cap"}

    # PORTFOLIO: more unique tickets is the one honest way to raise the chance
    # of hitting a huge field. Each extra lineup must differ from every
    # accepted one by >= `uniq` players, and no player may appear in more than
    # ~60% of them once there are enough for that to mean something.
    n_lineups = max(1, min(20, int(n_lineups or 1)))
    uniq = max(1, min(6, int(uniq or 2)))
    if pk and pk.force and uniq > len(ROSTER) - len(pk.force):
        # the overlap rule already counts only the free slots (every lineup
        # shares the forced men); past them it can never be met
        pk_notes.append(f"lineups differ by {max(1, len(ROSTER) - len(pk.force))} players, not {uniq}: your "
                        f"{len(pk.force)} forced players fill the rest")
        uniq = max(1, len(ROSTER) - len(pk.force))
    lineups = [lineup]
    if n_lineups > 1:
        import collections as _cl
        rosters = [frozenset(p["name"] for p in lineup)]
        expo = _cl.Counter(p["name"] for p in lineup)
        while len(lineups) < n_lineups:
            # The exposure cap is a CONSTRAINT, not a filter on the answer: the
            # search is seeded and exact, so a candidate rejected for crossing
            # 60% would come back identical on every retry and the portfolio
            # would end short. A name that would cross it sits this one out.
            pool_k = ours
            if len(lineups) >= 3:
                capped = {nm for nm, c in expo.items() if (c + 1) / (len(lineups) + 1) > 0.6}
                # the forced men are in every lineup by definition: capped, the
                # fourth lineup could not be built
                capped -= set(pk.force) if pk else set()
                pool_k = [p for p in ours if p["name"] not in capped]
            cand = optimize(pool_k, CAP, objective, stack_min=stack_min,
                            bring_min=bring_min, restarts=1000,
                            exclude=(rosters, uniq), seed=len(lineups), fixed=fixed,
                            # the defense kick is the headline lineup's (up to
                            # 3 s on a 12-game slate); twenty of them would sit
                            # a web worker on one core for a minute
                            thorough=False)
            if not cand:
                break                     # nothing legal is left at this uniqueness
            names = frozenset(p["name"] for p in cand)
            lineups.append(cand)
            rosters.append(names)
            expo.update(names)

    # lineup distribution from the correlated arrays
    L = min(len(p["arr"]) for p in lineup)
    totals = [sum(p["arr"][i] for p in lineup) for i in range(L)]
    csim = None
    if contest:
        try:
            # The sample box NEVER REACHED this call -- every NFL contest sim
            # ran at 500 opponents whatever the user typed, and a million-entry
            # GPP is decided in a tail 500 lineups cannot map. Clamped, because
            # the cost is linear and 3000 is already a ~20s build. One FIELD is
            # sampled and every portfolio lineup is scored against it.
            csim = contest_sim(lineup, ours, contest=contest, entry_fee=entry_fee,
                               contest_size=contest_size, prize_pool=prize_pool,
                               first_prize=first_prize,
                               sample_size=max(200, min(3000, int(field_size or 500))),
                               extra_lineups=lineups[1:], payouts=payouts,
                               field_players=(players if pk else None), ref=unpicked_proj)
        except Exception as _e:
            # the card drops its contest lines; the ledger says why
            errlog.note("NFLDFS-contest", _e)
            csim = None
    # Sample-noise probe (dfs_backtest.run_sample): the same lineup against
    # fields of the asked sizes and seeds at the asked entry counts.
    probe_rows = None
    if contest_probe:
        probe_rows = []
        for pr in contest_probe:
            try:
                cs = contest_sim(lineup, players, contest=(contest or "gpp"), entry_fee=entry_fee,
                                 contest_size=pr.get("entries") or contest_size,
                                 sample_size=max(150, min(3000, int(pr.get("sample") or 500))),
                                 seed=pr.get("seed"))
                if cs:
                    row = dict(pr, **{k: cs.get(k) for k in ("win_pct", "cash_pct", "top1_pct", "roi_pct")})
                    row["field"], row["field_hash"] = cs.get("sample_size"), cs.get("field_hash")
                else:
                    row = dict(pr, error="no field")
            except Exception as e:
                row = dict(pr, error=str(e))
            probe_rows.append(row)
    def _rows(lu):
        order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "FLEX": 4, "DST": 5}
        need = {"RB": 2, "WR": 3, "TE": 1}
        seen = {"RB": 0, "WR": 0, "TE": 0}
        out = []
        for p in sorted(lu, key=lambda x: (order.get(x["pos"], 9), -x["proj"])):
            slot = p["pos"]
            if p["pos"] in need:
                seen[p["pos"]] += 1
                if seen[p["pos"]] > need[p["pos"]]:
                    slot = "FLEX"
            out.append({"slot": slot, "name": p["name"], "pos": p["pos"], "team": p["team"],
                        "depth": p.get("depth"),
                        "salary": p["salary"], "proj": p["proj"], "ceiling": p["ceiling"],
                        "floor": p["floor"], "own": p.get("own")})
        return out
    rows = _rows(lineup)
    _sk = _status_seen(csv_players)
    return {"mode": "classic", "roster": ROSTER,
            "status_known": _sk,
            "status_warning": None if _sk else
            ("DK's Status column isn't in this paste, so OUT and IR players "
             "could NOT be excluded - check every name is active before you "
             "enter. Copy the whole CSV including its header row."),
            "week": week, "objective": objective, "stack": bool(stack_min),
            "bring_back": bool(bring_min),
            "slate_mode": slate_note,
            "lineups": ([{"lineup": _rows(lu),
                          "total_salary": sum(p["salary"] for p in lu),
                          "total_proj": round(sum(p["proj"] for p in lu), 1)}
                         for lu in lineups] if len(lineups) > 1 else None),
            "n_lineups": len(lineups),
            "salary": sum(p["salary"] for p in lineup), "cap": CAP,
            "proj": round(sum(p["proj"] for p in lineup), 1),
            "floor": round(_pct(totals, 0.10), 1), "median": round(_pct(totals, 0.50), 1),
            "ceiling": round(_pct(totals, 0.90), 1), "max": round(max(totals), 1),
            "lineup": rows, "contest_sim": csim, "contest_probe": probe_rows,
            "unmatched": unmatched[:20],
            "excluded": excluded[:40], "n_excluded": len(excluded),
            "roster_source": roster_state(),
            "inputs": nfl_dfs_sim.week_stamp(week, preseason),
            "n_pool": len(players),
            **({"pick": pick_stamp(pk, waived, pk_notes),
                "unpicked_best": round(unpicked_proj, 1) if unpicked_proj is not None else None} if pk else {}),
            "note": "Projections pinned to Sleeper; floor/ceiling + QB-WR correlation from the "
                    "game sim. Ownership is a model estimate of the field. In season the "
                    "pool is gated by Sleeper's depth chart: QB1, RB1-2, WR1-3, TE1-2 per "
                    "team -- no practice squad, no unprojected fliers."}
