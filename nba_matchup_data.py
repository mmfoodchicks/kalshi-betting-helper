"""NBA matchup engine, the data layer: one ESPN game summary -> compact
per-player-game and per-team-game rows (pure Python; the server and the PC
both run it).

WHAT THE SUMMARY CARRIES, and what this module keeps (measured 2026-10-23 on
2022-23 .. 2025-26 summaries):

  box score    minutes (whole minutes), points, FG / 3PT / FT made-attempted,
               OREB / DREB, assists, turnovers, steals, blocks, fouls, +/-;
               the starter flag; ESPN's position (G / F / C on ~97% of rows,
               PG / SG / SF / PF on the rest -- grouped to G / F / C here);
               didNotPlay with ESPN's reason.
  plays        everything nba_pbp keeps (thirteen ints a play) PLUS the full
               shot TYPE TEXT, which is the league's own action descriptor:
               "Pullup Jump Shot", "Step Back Jump Shot", "Driving Layup
               Shot", "Cutting Dunk Shot", "Alley Oop Dunk Shot", "Layup Shot
               Putback", "Turnaround Hook Shot" (51 distinct texts in 2025-26).
               Those words are OBSERVED (charted by the league's scorers);
               "catch-and-shoot" is NOT a word the feed uses, so it is a
               PROXY here: a plain "Jump Shot" (no pull-up / step-back /
               fade / turnaround / running / driving word) and, for makes,
               the assist flag.
  substitutions  "X enters the game for Y" (participants: entering, leaving),
               which with the box's five starters gives every on-court stint.

NOT AVAILABLE (stats.nba.com / cdn.nba.com do not answer this host): the
league's tracking (touches, drives, potential assists, contested shots,
rebound chances as the league defines them) and synergy play types
(pick-and-roll ball handler / roll man, isolation, post-up, spot-up). The
defensive SCHEME (drop / switch / hedge / blitz / zone) is not observable at
all; what IS observable is each defense's footprint -- the shot mix it
concedes, how many of the makes against it are assisted, its free throws,
the offensive rebounds and early-clock shots it allows, its pace -- and that
is all this module measures. See nba_matchup_profiles.external_profiles for
the optional PC-side fetch that could add the league's own play types.

Rows (array('i'), field order PF / TF below) are what the season state keeps;
the plays themselves are dropped after a game is cut (a season of rows is
~2.5 MB; the plays ~28 MB).

Derived definitions (every one a proxy unless the feed states it):

  on-court stints  starters (box) open the game; each later period's five are
                   the players whose first event in the period is not their
                   own entry (subbed OUT, or acting) -- the feed records no
                   between-period changes. Measured against the box's
                   minutes: see research/reports/nba_matchup.md.
  rebound chances  every rebound event (player or team) after a live miss;
                   the shooting side's five get an OREB chance, the other
                   five a DREB chance. The team rebound the feed writes after
                   a missed NON-final free throw is bookkeeping (the ball is
                   dead) and is not a chance.
  possessions      nba_poss's rules (a make without an and-one, a defensive
                   rebound, a turnover, the last free throw of a trip made,
                   a period end), counted for the five on the floor at the
                   possession's LAST play (substitutions happen at dead balls
                   between possessions).
  early clock      a possession's FIRST shot taken within EARLY_S seconds of a
                   live-ball start (a defensive rebound or a steal): the
                   transition PROXY (no tracking of the defense's set).
  second chance    a shot in a possession after the offence's own offensive
                   rebound.
"""
import array
import datetime as _dt
import time as _time

import errlog
import nba_pbp as P

SCHEMA = 1                 # bump with any change to a row's field list or a definition
EARLY_S = 7                # seconds after a live-ball start that count as "early clock"

# ---- shot descriptors from the type text -------------------------------------
X_PULLUP, X_STEPBACK, X_FADE, X_TURN, X_DRIVE, X_CUT, X_RUN, X_ALLEY, X_PUTBACK, X_FLOAT, X_HOOK, \
    X_BANK, X_DUNK, X_LAYUP, X_JUMP, X_HEAVE, X_REVERSE, X_POST = (1 << i for i in range(18))
_WORDS = ((X_PULLUP, ("pullup", "pull-up", "pull up")), (X_STEPBACK, ("step back", "stepback")),
          (X_FADE, ("fade",)), (X_TURN, ("turnaround",)), (X_DRIVE, ("driving",)), (X_CUT, ("cutting",)),
          (X_RUN, ("running",)), (X_ALLEY, ("alley oop", "alley-oop")), (X_PUTBACK, ("putback", "tip")),
          (X_FLOAT, ("float",)), (X_HOOK, ("hook",)), (X_BANK, ("bank",)), (X_DUNK, ("dunk",)),
          (X_LAYUP, ("layup", "finger roll")), (X_JUMP, ("jump shot", "jumper")), (X_HEAVE, ("heave",)),
          (X_REVERSE, ("reverse",)), (X_POST, ("post",)))
# a jumper off the dribble or a move: the feed's own words
X_SELF_JUMPER = X_PULLUP | X_STEPBACK | X_FADE | X_TURN
# a jumper with no dribble / move word: the catch-and-shoot PROXY
_NOT_SPOT = X_SELF_JUMPER | X_RUN | X_DRIVE | X_FLOAT | X_HOOK | X_HEAVE


def shot_bits(type_text):
    t = (type_text or "").lower()
    b = 0
    for bit, words in _WORDS:
        if any(w in t for w in words):
            b |= bit
    return b


def is_spot_jumper(bits):
    return bool(bits & X_JUMP) and not (bits & _NOT_SPOT)


POS_GROUPS = ("G", "F", "C")


def pos_group(abbr):
    """ESPN's position -> G / F / C. 'G-F' style hybrids take the first."""
    a = (abbr or "").upper().strip()
    if not a:
        return -1
    a = a.split("-")[0].split("/")[0]
    if a in ("G", "PG", "SG"):
        return 0
    if a in ("F", "SF", "PF"):
        return 1
    if a == "C":
        return 2
    return -1


# ---- the parse -----------------------------------------------------------------
_BOX_KEYS = {"minutes": "min", "points": "pts", "fieldGoalsMade-fieldGoalsAttempted": "fg",
             "threePointFieldGoalsMade-threePointFieldGoalsAttempted": "tp",
             "freeThrowsMade-freeThrowsAttempted": "ft", "rebounds": "reb", "assists": "ast",
             "turnovers": "tov", "steals": "stl", "blocks": "blk", "offensiveRebounds": "oreb",
             "defensiveRebounds": "dreb", "fouls": "pf", "plusMinus": "pm"}


def _num(s):
    try:
        return int(float(str(s).replace("+", "")))
    except (TypeError, ValueError):
        return 0


def _pair(s):
    try:
        a, b = str(s).split("-")
        return int(a), int(b)
    except (TypeError, ValueError):
        return 0, 0


def parse_box(d, side_of_team):
    """[{pid, side, name, pos, pos_g, starter, played, dnp_reason, min, pts, fgm, fga, tpm, tpa,
    ftm, fta, oreb, dreb, reb, ast, tov, stl, blk, pf, pm}] -- every athlete ESPN lists."""
    out = []
    for t in (d.get("boxscore") or {}).get("players") or []:
        s = side_of_team.get(str((t.get("team") or {}).get("id")))
        if s is None:
            continue
        for st in t.get("statistics") or []:
            keys = [_BOX_KEYS.get(k) for k in st.get("keys") or []]
            for a in st.get("athletes") or []:
                ath = a.get("athlete") or {}
                pid = P._int(ath.get("id"))
                if not pid:
                    continue
                vals = dict(zip(keys, a.get("stats") or []))
                fgm, fga = _pair(vals.get("fg"))
                tpm, tpa = _pair(vals.get("tp"))
                ftm, fta = _pair(vals.get("ft"))
                mn = _num(vals.get("min"))
                stats = (fga, fta, _num(vals.get("reb")), _num(vals.get("ast")), _num(vals.get("tov")),
                         _num(vals.get("pf")), _num(vals.get("stl")), _num(vals.get("blk")))
                played = (not a.get("didNotPlay")) and bool(a.get("stats")) and (mn > 0 or any(stats))
                pos = (ath.get("position") or {}).get("abbreviation") or ""
                out.append({"pid": pid, "side": s, "name": ath.get("displayName") or "", "pos": pos,
                            "pos_g": pos_group(pos), "starter": 1 if a.get("starter") else 0,
                            "played": 1 if played else 0,
                            "dnp_reason": (a.get("reason") or "") if a.get("didNotPlay") else "",
                            "min": mn, "pts": _num(vals.get("pts")), "fgm": fgm, "fga": fga, "tpm": tpm,
                            "tpa": tpa, "ftm": ftm, "fta": fta, "oreb": _num(vals.get("oreb")),
                            "dreb": _num(vals.get("dreb")), "reb": _num(vals.get("reb")),
                            "ast": _num(vals.get("ast")), "tov": _num(vals.get("tov")),
                            "stl": _num(vals.get("stl")), "blk": _num(vals.get("blk")),
                            "pf": _num(vals.get("pf")), "pm": _num(vals.get("pm"))})
    return out


def parse_game(d):
    """One summary -> the matchup record: nba_pbp's record plus `xt` (one shot
    descriptor int per play, aligned with the plays) and `box`. None when the
    summary has no plays, no final score or no box."""
    rec = P.parse_summary(d)
    if not rec:
        return None
    xt = array.array("i")
    for p in d.get("plays") or []:
        ty = (p.get("type") or {}).get("text") or ""
        xt.append(shot_bits(ty) if p.get("shootingPlay") and not ty.lower().startswith("free throw") else 0)
    if len(xt) * P.PLAY_W != len(rec["plays"]):
        return None
    # The 2021-22 feed carries no `pointsAttempted` (0 on every shot) and
    # writes some threes without the words "three point" ("makes 25-foot step
    # back jumpshot"): nba_pbp then reads them as twos and 1,051 of 1,230
    # games failed the points check. A make's value is its scoreValue; a
    # miss's is three from 23 feet out (the arc is 22 ft in the corners,
    # 23.75 above the break). Only shots WITHOUT pointsAttempted are touched
    # (every shot from 2022-23 on carries it).
    raw = d.get("plays") or []
    a = rec["plays"]
    patched = 0
    for i, p in enumerate(raw):
        j = i * P.PLAY_W
        if a[j + P.I_CODE] != P.FGA or P._int(p.get("pointsAttempted")):
            continue
        sv = P._int(p.get("scoreValue"))
        if a[j + P.I_MADE] and sv in (2, 3):
            patched += a[j + P.I_VAL] != sv
            a[j + P.I_VAL] = sv
        elif not a[j + P.I_MADE] and a[j + P.I_VAL] == 2 and a[j + P.I_DIST] >= 23:
            patched += 1
            a[j + P.I_VAL] = 3
    if patched:
        rec["feed"] = f"old: {patched} shot values from scoreValue / distance"
    side_of_team = {rec["home_id"]: 0, rec["away_id"]: 1}
    box = parse_box(d, side_of_team)
    if not box:
        return None
    rec["xt"] = xt
    rec["box"] = box
    return rec


# ---- the walk: possessions, stints, chances ------------------------------------
def _period_len(per):
    return 720 if per <= 4 else 300


def _actors(p):
    """Players a play shows ON THE FLOOR: the primary participant and the
    secondary (assister, blocker, stealer, the player fouled). Technical fouls
    and ejections can name the bench and are not evidence."""
    if p[P.I_CODE] in (P.FOUL_TECH, P.EJECTION, P.TIMEOUT, P.END_PERIOD, P.END_GAME, P.OTHER, P.REVIEW):
        return ()
    if p[P.I_CODE] == P.SUB:
        return ()
    return tuple(x for x in (p[P.I_P1], p[P.I_P2]) if x)


PF = ("gid", "pid", "side", "starter", "pos_g", "played", "min", "secs", "pts", "fgm", "fga", "tpm", "tpa",
      "ftm", "fta", "oreb", "dreb", "ast", "tov", "stl", "blk", "pf", "pm",
      "poss_off", "poss_def", "tm_fga", "tm_fta", "tm_tov", "tm_fgm", "tm_pts", "oreb_ch", "dreb_ch",
      "op_fga", "op_fgm", "op_pts",
      "a_rim", "m_rim", "a_short", "m_short", "a_mid", "m_mid", "a_3", "m_3", "ast_m2", "ast_m3",
      "pull_a", "pull_m", "pull3_a", "pull3_m", "spot_a", "spot3_a", "spot3_m", "drive_a", "drive_m",
      "cut_a", "cut_m", "putback_a", "putback_m", "post_a", "post_m", "sc_a", "sc_pts", "tr_a", "tr_pts",
      "ast_rim", "ast_3", "sfd", "heave_a", "pbp_pts", "pbp_reb", "pbp_ast")
PI = {k: i for i, k in enumerate(PF)}
# a team's OFFENSE in one game (its defense's concessions are the other side's row)
TF = ("gid", "side", "won", "secs", "poss", "pts", "fga", "fgm", "a_rim", "m_rim", "a_short", "m_short",
      "a_mid", "m_mid", "a_3", "m_3", "ast_m", "ast_m3", "unast_m3", "unast_m2", "pull_a", "pull_m",
      "pull3_a", "spot3_a", "spot3_m", "drive_a", "cut_a", "putback_a", "post_a", "fta", "ftm", "tov",
      "tov_live", "oreb", "dreb", "miss", "oreb_ch", "tr_a", "tr_pts", "sc_a", "sc_pts", "ast", "periods",
      "early_poss", "live_starts")
TI = {k: i for i, k in enumerate(TF)}


def _zone(p):
    if p[P.I_VAL] == 3:
        return 3
    d = p[P.I_DIST]
    if d < 0 or d <= 3:
        return 0
    return 1 if d <= 13 else 2


_ZA = ("a_rim", "a_short", "a_mid", "a_3")
_ZM = ("m_rim", "m_short", "m_mid", "m_3")


def cut_game(rec):
    """The matchup rows of one game: (player rows {pid: array}, team rows
    (home, away) arrays, quality {}) -- every derived definition in the
    module docstring, in one walk of the plays."""
    plays = list(P.iter_plays(rec))
    xt = rec["xt"]
    gid = int(rec["id"])
    side_of = {b["pid"]: b["side"] for b in rec["box"]}
    for pid, s in (rec.get("players") or {}).items():
        side_of.setdefault(pid, s)
    R = {}
    for b in rec["box"]:
        r = array.array("i", [0] * len(PF))
        r[PI["gid"]], r[PI["pid"]], r[PI["side"]] = gid, b["pid"], b["side"]
        r[PI["starter"]], r[PI["pos_g"]], r[PI["played"]] = b["starter"], b["pos_g"], b["played"]
        for k in ("min", "pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "ast", "tov",
                  "stl", "blk", "pf", "pm"):
            r[PI[k]] = b[k]
        R[b["pid"]] = r
    T = [array.array("i", [0] * len(TF)) for _ in (0, 1)]
    for s in (0, 1):
        T[s][TI["gid"]], T[s][TI["side"]] = gid, s
    hs, as_ = rec["home_score"], rec["away_score"]
    T[0][TI["won"]], T[1][TI["won"]] = int(hs > as_), int(as_ > hs)
    T[0][TI["periods"]] = T[1][TI["periods"]] = rec.get("periods") or 4
    q = {"sub_anom": 0, "short_five": 0, "secs_team": [0, 0], "unknown_actor": 0}

    def row(pid):
        return R.get(pid)

    def bump(pid, k, v=1):
        r = R.get(pid)
        if r is not None:
            r[PI[k]] += v

    on = [set(), set()]
    # possession state
    off = None             # side with the ball
    p_start = 720          # clock the possession began
    p_kind = "period"      # how it began
    p_oreb = False         # an offensive rebound has happened in it
    p_shot = False         # a field goal attempt has happened in it (for the early-clock first shot)
    last_play = None
    cur_per = None
    clk = 720
    by_per = {}
    for i, p in enumerate(plays):
        by_per.setdefault(p[P.I_PER], []).append(i)
    starters = [set(), set()]
    for b in rec["box"]:
        if b["starter"]:
            starters[b["side"]].add(b["pid"])

    def five_for(per, prev):
        first = {}
        for i in by_per.get(per, ()):
            p = plays[i]
            if p[P.I_CODE] == P.SUB:
                for pid, how in ((p[P.I_P1], "in"), (p[P.I_P2], "out")):
                    if pid and pid not in first:
                        first[pid] = how
                continue
            for a in _actors(p):
                if a not in first:
                    first[a] = "act"
        sets = [set(), set()]
        for pid, how in first.items():
            s = side_of.get(pid)
            if s is None:
                continue
            if how != "in":
                sets[s].add(pid)
        if per == 1:
            for s in (0, 1):
                if len(starters[s]) == 5:
                    sets[s] = set(starters[s])
        order = {pid: j for j, pid in enumerate(first)}
        for s in (0, 1):
            if len(sets[s]) < 5:
                for x in sorted(prev[s]):
                    if len(sets[s]) >= 5:
                        break
                    if x not in first:
                        sets[s].add(x)
            if len(sets[s]) < 5:
                q["short_five"] += 1
            if len(sets[s]) > 5:
                sets[s] = set(sorted(sets[s], key=lambda x: order.get(x, 1 << 30))[:5])
        return sets

    def credit_secs(dt):
        if dt <= 0:
            return
        for s in (0, 1):
            q["secs_team"][s] += dt * len(on[s])
            for pid in on[s]:
                bump(pid, "secs", dt)

    def end_poss(new_kind, new_off, at_clk):
        nonlocal off, p_start, p_kind, p_oreb, p_shot
        if off is not None:
            T[off][TI["poss"]] += 1
            for pid in on[off]:
                bump(pid, "poss_off")
            for pid in on[1 - off]:
                bump(pid, "poss_def")
        off = new_off
        p_start, p_kind, p_oreb, p_shot = at_clk, new_kind, False, False
        if new_off is not None and new_kind in ("dreb", "steal"):
            T[new_off][TI["live_starts"]] += 1

    for i, p in enumerate(plays):
        per, pclk, side, code = p[P.I_PER], p[P.I_CLK], p[P.I_TEAM], p[P.I_CODE]
        if per != cur_per:
            if cur_per is not None:
                credit_secs(clk)          # the rest of the previous period
                end_poss("period", None, _period_len(per))
            prev = (set(on[0]), set(on[1]))
            on = five_for(per, prev)
            cur_per, clk = per, _period_len(per)
            off, p_start, p_kind, p_oreb, p_shot = None, clk, "period", False, False
        if pclk <= clk:
            credit_secs(clk - pclk)
            clk = pclk
        if code in (P.END_PERIOD, P.END_GAME):
            last_play = p
            continue
        if code == P.SUB:
            pin, pout = p[P.I_P1], p[P.I_P2]
            s = side_of.get(pin, side_of.get(pout))
            if s in (0, 1):
                if pout in on[s]:
                    on[s].discard(pout)
                else:
                    q["sub_anom"] += 1
                if pin in on[s]:
                    q["sub_anom"] += 1
                on[s].add(pin)
            continue
        if code in (P.TIMEOUT, P.REVIEW, P.EJECTION, P.JUMPBALL, P.OTHER, P.FOUL_TECH, P.VIOLATION):
            continue
        if side not in (0, 1):
            continue
        # who has the ball on this play
        if code == P.DREB:
            # a rebound after a missed NON-final free throw is bookkeeping
            if last_play is not None and last_play[P.I_CODE] == P.FT and not last_play[P.I_MADE] \
                    and divmod(last_play[P.I_VAL], 10)[0] < divmod(last_play[P.I_VAL], 10)[1]:
                last_play = p
                continue
            for pid in on[1 - side]:
                bump(pid, "oreb_ch")
            for pid in on[side]:
                bump(pid, "dreb_ch")
            T[1 - side][TI["oreb_ch"]] += 1
            T[side][TI["dreb"]] += 1
            if not (p[P.I_FLAGS] & P.F_TEAM):
                bump(p[P.I_P1], "pbp_reb")
            end_poss("dreb", side, pclk)
            last_play = p
            continue
        o = None
        if code in (P.FGA, P.FT, P.OREB, P.TOV, P.FOUL_OFF):
            o = side
        elif code in (P.FOUL_SHOOT, P.FOUL_PERS, P.FOUL_TAKE, P.FOUL_FLAG):
            o = 1 - side
        if o is None:
            last_play = p
            continue
        if off != o:
            end_poss("other" if off is not None else p_kind, o, p_start if off is None else pclk)
        if code == P.OREB:
            if last_play is not None and last_play[P.I_CODE] == P.FT and not last_play[P.I_MADE] \
                    and divmod(last_play[P.I_VAL], 10)[0] < divmod(last_play[P.I_VAL], 10)[1]:
                last_play = p
                continue
            for pid in on[o]:
                bump(pid, "oreb_ch")
            for pid in on[1 - o]:
                bump(pid, "dreb_ch")
            T[o][TI["oreb_ch"]] += 1
            T[o][TI["oreb"]] += 1
            if not (p[P.I_FLAGS] & P.F_TEAM):
                bump(p[P.I_P1], "pbp_reb")
            p_oreb = True
        elif code == P.FGA:
            shooter, made, val = p[P.I_P1], p[P.I_MADE], p[P.I_VAL]
            z = _zone(p)
            bits = xt[i]
            heave = bool(bits & X_HEAVE)
            t = T[o]
            elapsed = p_start - pclk
            early = (not p_shot) and (not p_oreb) and p_kind in ("dreb", "steal") and 0 <= elapsed <= EARLY_S
            p_shot = True
            if shooter not in side_of and shooter:
                q["unknown_actor"] += 1
            t[TI["fga"]] += 1
            t[TI[_ZA[z]]] += 1
            for pid in on[o]:
                bump(pid, "tm_fga")
            for pid in on[1 - o]:
                bump(pid, "op_fga")
            if heave:
                bump(shooter, "heave_a")
            else:
                bump(shooter, _ZA[z])
            self_j = bool(bits & X_SELF_JUMPER) and bool(bits & X_JUMP)
            spot = is_spot_jumper(bits)
            if self_j and not heave:
                bump(shooter, "pull_a")
                t[TI["pull_a"]] += 1
                if z == 3:
                    bump(shooter, "pull3_a")
                    t[TI["pull3_a"]] += 1
            if spot:
                bump(shooter, "spot_a")
                if z == 3:
                    bump(shooter, "spot3_a")
                    t[TI["spot3_a"]] += 1
            drive = bool(bits & X_DRIVE) and z in (0, 1)
            cut = bool(bits & (X_CUT | X_ALLEY))
            putback = bool(bits & X_PUTBACK)
            post = bool(bits & X_HOOK) or (bool(bits & (X_TURN | X_POST)) and z in (0, 1))
            for flag, k, tk in ((drive, "drive", "drive_a"), (cut, "cut", "cut_a"), (putback, "putback", "putback_a"),
                                (post, "post", "post_a")):
                if flag:
                    bump(shooter, k + "_a")
                    t[TI[tk]] += 1
            if p_oreb:
                bump(shooter, "sc_a")
                t[TI["sc_a"]] += 1
            if early:
                bump(shooter, "tr_a")
                t[TI["tr_a"]] += 1
                T[o][TI["early_poss"]] += 1
            if made:
                t[TI["fgm"]] += 1
                t[TI[_ZM[z]]] += 1
                t[TI["pts"]] += val
                bump(shooter, "pbp_pts", val)
                for pid in on[o]:
                    bump(pid, "tm_fgm")
                    bump(pid, "tm_pts", val)
                for pid in on[1 - o]:
                    bump(pid, "op_fgm")
                    bump(pid, "op_pts", val)
                if not heave:
                    bump(shooter, _ZM[z])
                assisted = bool(p[P.I_FLAGS] & P.F_ASSISTED)
                if assisted:
                    t[TI["ast_m"]] += 1
                    t[TI["ast"]] += 1
                    bump(shooter, "ast_m3" if val == 3 else "ast_m2")
                    bump(p[P.I_P2], "pbp_ast")
                    if z == 0:
                        bump(p[P.I_P2], "ast_rim")
                    if val == 3:
                        bump(p[P.I_P2], "ast_3")
                        t[TI["ast_m3"]] += 1
                elif val == 3:
                    t[TI["unast_m3"]] += 1
                else:
                    t[TI["unast_m2"]] += 1
                if self_j and not heave:
                    bump(shooter, "pull_m")
                    t[TI["pull_m"]] += 1
                    if z == 3:
                        bump(shooter, "pull3_m")
                if spot and z == 3:
                    bump(shooter, "spot3_m")
                    t[TI["spot3_m"]] += 1
                for flag, k in ((drive, "drive_m"), (cut, "cut_m"), (putback, "putback_m"), (post, "post_m")):
                    if flag:
                        bump(shooter, k)
                if p_oreb:
                    bump(shooter, "sc_pts", val)
                    t[TI["sc_pts"]] += val
                if early:
                    bump(shooter, "tr_pts", val)
                    t[TI["tr_pts"]] += val
                # an and-one keeps the ball for the free throw
                and1 = False
                for qq in plays[i + 1:i + 8]:
                    if qq[P.I_CLK] != pclk:
                        break
                    if qq[P.I_CODE] == P.FT and qq[P.I_TEAM] == o and qq[P.I_VAL] == 11:
                        and1 = True
                        break
                if not and1:
                    end_poss("make", 1 - o, pclk)
            else:
                t[TI["miss"]] += 1
        elif code == P.FT:
            k, n = divmod(p[P.I_VAL], 10)
            made = p[P.I_MADE]
            t = T[o]
            t[TI["fta"]] += 1
            for pid in on[o]:
                bump(pid, "tm_fta")
            if made:
                t[TI["ftm"]] += 1
                t[TI["pts"]] += 1
                bump(p[P.I_P1], "pbp_pts", 1)
                for pid in on[o]:
                    bump(pid, "tm_pts")
                for pid in on[1 - o]:
                    bump(pid, "op_pts")
            retained = p[P.I_FLAGS] & (P.F_FLAGRANT_FT | P.F_TECH_FT | P.F_CLEAR)
            if k == n and made and not retained:
                end_poss("ft", 1 - o, pclk)
        elif code == P.TOV:
            t = T[o]
            t[TI["tov"]] += 1
            live = bool(p[P.I_FLAGS] & P.F_STEAL)
            if live:
                t[TI["tov_live"]] += 1
            for pid in on[o]:
                bump(pid, "tm_tov")
            end_poss("steal" if live else "dead_tov", 1 - o, pclk)
        elif code == P.FOUL_SHOOT:
            if p[P.I_P2]:
                bump(p[P.I_P2], "sfd")
        last_play = p
    credit_secs(clk)
    end_poss("period", None, 0)
    for s in (0, 1):
        T[s][TI["secs"]] = int(q["secs_team"][s] / 5)
    return R, (T[0], T[1]), q


# ---- the per-game summary the season state keeps ----------------------------
def game_entry(rec):
    """{id, date, season, home, away, scores, periods, players: {pid: row}, teams: (home, away),
    names {pid: name}, pos {pid: abbr}, dnp {pid: reason}, quality} -- the plays dropped."""
    R, T, q = cut_game(rec)
    names = {b["pid"]: b["name"] for b in rec["box"]}
    pos = {b["pid"]: b["pos"] for b in rec["box"]}
    dnp = {b["pid"]: b["dnp_reason"] for b in rec["box"] if b["dnp_reason"]}
    return {"id": str(rec["id"]), "date": rec.get("date"), "season": rec.get("season"), "st": rec.get("st"),
            "home": rec["home"], "away": rec["away"], "home_score": rec["home_score"],
            "away_score": rec["away_score"], "periods": rec.get("periods"),
            "players": R, "teams": T, "names": names, "pos": pos, "dnp": dnp, "quality": q}


def entry_ok(e):
    """The quality gate a cut game passes before any profile reads it: the
    feed's points reproduce the box's for every player who played, and each
    side's on-court seconds came out at five players' worth of the clock
    (a stint inference that lost a player shows here)."""
    for r in e["players"].values():
        if r[PI["played"]] and r[PI["pbp_pts"]] != r[PI["pts"]]:
            return False
    secs = 2880 + 300 * max(0, (e.get("periods") or 4) - 4)
    for t in e["teams"]:
        if abs(t[TI["secs"]] - secs) > 2:
            return False
    return True


# ---- season state (production: the nightly job) ------------------------------
STATE_KEY = "nba_matchup_{season}"


def season_of(date_iso):
    y, m = int(date_iso[:4]), int(date_iso[5:7])
    return y if m >= 8 else y - 1


def load_state(season):
    import deep_cache
    st, _ts = deep_cache.load(STATE_KEY.format(season=int(season)))
    if not isinstance(st, dict) or st.get("schema") != SCHEMA:
        return {"schema": SCHEMA, "season": int(season), "games": {}, "events": {}, "mismatch": [],
                "through": None, "updated": 0.0}
    return st


def update(season, today=None, max_fetch=250, log=None):
    """The nightly refresh (the job `nba_matchup`): the scoreboard for the
    days since the last run (the season's on a first run), each newly finished
    regular-season franchise game's summary fetched ONCE, cut into rows and
    gated (entry_ok); the plays are dropped. A game that fails the gate is
    recorded in `mismatch` and never read. Returns the state."""
    import deep_cache
    import racing
    st = load_state(season)
    today = today or _dt.date.today()
    start = _dt.date(int(season), 10, 1)
    if st.get("through"):
        start = max(start, _dt.date.fromisoformat(st["through"]) - _dt.timedelta(days=3))
    events = st.get("events") or {}
    day = start
    # a past season's walk stops at its last possible game (June 30), not today
    end = min(today, _dt.date(int(season) + 1, 6, 30))
    while day <= end:
        try:
            for ev in P._day_events(day.strftime("%Y%m%d")):
                if ev["espn_year"] == int(season) + 1 and ev["st"] in (2, 3, 5):
                    events[ev["id"]] = ev
        except Exception as e:
            errlog.note("NBAM-day", e, path=day.isoformat())
        day += _dt.timedelta(days=1)
    games = st.get("games") or {}
    mismatch = list(st.get("mismatch") or [])
    fetched = 0
    for g in sorted(events.values(), key=lambda g: (g.get("date") or "", g["id"])):
        if not P.league_game(g) or g.get("state") != "post" or g["id"] in games or g["id"] in mismatch:
            continue
        if fetched >= max_fetch:
            break
        try:
            d = racing._get_json(f"{P._SITE}/summary?event={g['id']}", timeout=40)
            rec = parse_game(d) if d else None
        except Exception as e:
            errlog.note("NBAM-game", e, path=str(g["id"]))
            continue
        fetched += 1
        if not rec or P.points_check(rec) != (rec["home_score"], rec["away_score"]):
            mismatch.append(g["id"])
            continue
        try:
            e = game_entry(rec)
        except Exception as ex:
            errlog.note("NBAM-cut", ex, path=str(g["id"]))
            mismatch.append(g["id"])
            continue
        if not entry_ok(e):
            mismatch.append(g["id"])
            continue
        e["date"] = g.get("date") or e["date"]
        games[g["id"]] = e
        if log and fetched % 100 == 0:
            log(f"[nba-matchup] {season}: {len(games)} games held")
    st.update(events=events, games=games, mismatch=mismatch, through=end.isoformat(), updated=_time.time())
    deep_cache.save(STATE_KEY.format(season=int(season)), st)
    return st
