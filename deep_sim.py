"""Deep pitch-by-pitch (really plate-appearance-by-plate-appearance) game engine.

Each at-bat samples a real outcome from the batter's and pitcher's per-PA rates
combined with the league baseline (log5 / odds-ratio), then walks a base-out
state machine. The two manager layers the design calls for live here:

  - Bullpen hook: the starter is pulled on workload (≈3x through the order) or
    when he's getting tagged; relievers enter worst-arm-first, each pulled when
    he tires or gets hit, and the closer is held back for a late lead.
  - Pinch hitters: late and close, a weak spot in the order due up is replaced
    by a better bench bat with the platoon edge (the offensive mirror of the
    bullpen hook). The sub then bats for the rest of the game.

Returns the final score plus a full box score (per-player batting and pitching
lines), which the season engine aggregates into stat-line distributions.
"""

import random

from deep_data import LG

# League per-PA outcome baseline (K/BB/HBP/HR + hit types); "out" is the remainder.
_OUT_KEYS = ("k", "bb", "hbp", "hr", "1b", "2b", "3b")
# Global calibration factors (tuned so a balanced league sample matches MLB
# per-game R/K/BB/H): trim the slightly-hot three-true-outcomes, lift balls in
# play so runs land near 4.4/game.
_K_CAL, _BB_CAL, _HIT_CAL = 0.95, 0.89, 1.10


def _log5(b, p, l):
    """Odds-ratio combination of a batter rate b and pitcher rate p vs league l."""
    if l <= 0 or l >= 1:
        return max(0.0, min(1.0, (b + p) / 2))
    num = (b * p) / l                          # log5: b=p=l -> l (no double-divide)
    den = num + ((1 - b) * (1 - p)) / (1 - l)
    return num / den if den > 0 else b


def _pa_probs(bat, pit):
    """Outcome probabilities for one plate appearance (sums to 1)."""
    br = bat["rates"]
    k = _log5(br["k"], pit["kpa"], LG["k"]) * _K_CAL
    bb = _log5(br["bb"], pit["bbpa"], LG["bb"]) * _BB_CAL
    hr = _log5(br["hr"], pit["hrpa"], LG["hr"])
    hbp = br["hbp"]
    # Remaining mass goes to balls in play (hits + outs), tilted by the pitcher's
    # run prevention (better ERA suppresses hits a touch).
    rest = max(0.0, 1.0 - k - bb - hr - hbp)
    qual = max(0.78, min(1.22, 4.30 / max(2.0, pit["era"])))  # >1 = pitcher worse
    s, d, t = br["1b"], br["2b"], br["3b"]
    hit = (s + d + t) / qual * _HIT_CAL  # better pitcher -> fewer hits in play
    bip = s + d + t + 0.0
    # batter's in-play out rate implied by his line (1 - all events)
    out_in_play = max(0.05, 1 - (br["k"] + br["bb"] + br["hbp"] + br["hr"] + bip))
    out_in_play *= qual                 # better pitcher -> more outs in play
    denom = hit + out_in_play
    p_hit = rest * (hit / denom) if denom else 0.0
    p_out = rest - p_hit
    hsum = s + d + t or 1.0
    return {"k": k, "bb": bb, "hbp": hbp, "hr": hr,
            "1b": p_hit * s / hsum, "2b": p_hit * d / hsum, "3b": p_hit * t / hsum,
            "out": p_out}


def _new_bat_line():
    return {"pa": 0, "ab": 0, "h": 0, "2b": 0, "3b": 0, "hr": 0, "bb": 0, "k": 0, "r": 0, "rbi": 0}


def _new_pit_line():
    return {"bf": 0, "outs": 0, "k": 0, "bb": 0, "h": 0, "hr": 0, "r": 0}


class _Staff:
    """A team's pitching staff during a game: starter, then bullpen with a hook."""

    def __init__(self, prof, starter):
        self.cur = starter
        self.bp = list(prof["bullpen"])      # worst-first; closer is last
        self.bp_i = 0
        self.closer_used = False
        self.outing_runs = 0                 # runs charged to the current pitcher's outing
        self.outing_bf = 0
        self.lines = {starter["id"]: _new_pit_line()}

    def maybe_hook(self, inning, lead):
        """Swap pitchers between batters per workload / damage / save situation."""
        p = self.cur
        is_starter = self.bp_i == 0 and not self.closer_used and p.get("gs", 0) >= 3
        if is_starter:
            # Performance-aware leash: a starter who's dealing earns more rope, a
            # gem (no-hitter / perfect game) almost never gets pulled on workload
            # alone, and a laboring starter comes out sooner.
            line = self.lines[p["id"]]
            baserunners = line["h"] + line["bb"]
            limit = 26
            if inning >= 6:
                if baserunners == 0:           # perfect game in progress — ride him
                    limit = 42
                elif line["h"] == 0:           # no-hitter (maybe a walk)
                    limit = 36
                elif baserunners <= 3:         # cruising, shutout-ish gem
                    limit = 30
                elif baserunners >= 9:          # laboring — quicker hook
                    limit = 22
            tired = self.outing_bf >= limit or (inning >= 6 and self.outing_bf >= limit - 6)
            tagged = self.outing_runs >= 5 or (inning >= 6 and self.outing_runs >= 4)
            # Never pull a no-hitter/perfect game on workload (only damage would).
            gem = inning >= 6 and line["h"] == 0
            if gem:
                tired = False
            if not (tired or tagged) or not self.bp:
                return
        else:
            # Reliever: ~ one inning, pulled sooner if tagged.
            if self.outing_bf < 4 and self.outing_runs < 2:
                return
        # Closer for the 9th+ with a save-sized lead.
        if inning >= 9 and 1 <= lead <= 3 and not self.closer_used and self.bp:
            nxt = self.bp[-1]
            self.bp = self.bp[:-1]
            self.closer_used = True
        elif self.bp_i < len(self.bp):
            nxt = self.bp[self.bp_i]
            self.bp_i += 1
        else:
            return                            # bullpen exhausted -> ride current arm
        self.cur = nxt
        self.outing_runs = self.outing_bf = 0
        self.lines.setdefault(nxt["id"], _new_pit_line())


def _advance(bases, outcome, batter, rng):
    """Mutate bases for a hit/walk. Bases hold the batter object on them so we can
    credit the run to whoever scores. Returns (runs, scorers)."""
    scorers = []
    if outcome == "bb" or outcome == "hbp":
        if bases[0] and bases[1] and bases[2]:
            scorers.append(bases[2])           # forced in
            bases[2] = bases[1]; bases[1] = bases[0]; bases[0] = batter
        elif bases[0] and bases[1]:
            bases[2] = bases[1]; bases[1] = bases[0]; bases[0] = batter
        elif bases[0]:
            bases[1] = bases[0]; bases[0] = batter
        else:
            bases[0] = batter
        return len(scorers), scorers
    if outcome == "hr":
        scorers = [b for b in bases if b] + [batter]
        bases[0] = bases[1] = bases[2] = None
        return len(scorers), scorers
    if outcome == "3b":
        scorers = [b for b in bases if b]
        bases[0] = bases[1] = None; bases[2] = batter
        return len(scorers), scorers
    if outcome == "2b":
        if bases[1]:
            scorers.append(bases[1])
        if bases[2]:
            scorers.append(bases[2])
        new = [None, batter, None]             # batter to 2nd
        if bases[0]:
            if rng.random() < 0.45:            # runner from 1st scores on the double
                scorers.append(bases[0])
            else:
                new[2] = bases[0]              # else holds at 3rd
        bases[0], bases[1], bases[2] = new
        return len(scorers), scorers
    # single: runner on 3rd scores; runner on 2nd scores ~55% else to 3rd;
    # runner on 1st to 2nd; batter to 1st.
    if bases[2]:
        scorers.append(bases[2])
    new = [batter, None, None]
    if bases[1]:
        if rng.random() < 0.62:                # runner from 2nd scores on the single
            scorers.append(bases[1])
        else:
            new[2] = bases[1]
    if bases[0]:
        new[1] = bases[0]                      # runner from 1st to 2nd
    bases[0], bases[1], bases[2] = new
    return len(scorers), scorers


def _pick_ph(bench, due, pitcher_hand, used):
    """A better bench bat with the platoon edge, or None to let `due` hit."""
    best, best_gain = None, 0.0
    due_val = due["rates"]["1b"] + 2 * due["rates"]["2b"] + 4 * due["rates"]["hr"] + due["rates"]["bb"]
    for b in bench:
        if b["id"] in used:
            continue
        plat = 1.08 if (b["side"] in ("L", "S")) != (pitcher_hand == "L") else 1.0
        val = (b["rates"]["1b"] + 2 * b["rates"]["2b"] + 4 * b["rates"]["hr"] + b["rates"]["bb"]) * plat
        if val - due_val > best_gain:
            best, best_gain = b, val - due_val
    return best if best_gain > 0.03 else None


def _avail_sp(sp, prof, rng):
    """If the scheduled starter is on a short IL and 'misses' this start, hand the
    ball to the next available rotation arm (a spot start)."""
    if sp and sp.get("avail", 1.0) < 1.0 and rng.random() > sp["avail"]:
        for alt in prof["rotation"]:
            if alt["id"] != sp["id"] and alt.get("avail", 1.0) >= 1.0:
                return alt
    return sp


def play_game(home, away, sp_home=None, sp_away=None, rng=None):
    """Play one game. `home`/`away` are team_profiles; SPs default to rotation[0].
    Returns {home_runs, away_runs, home_win, batting:{pid:line}, pitching:{pid:line}}."""
    rng = rng or random
    sp_home = _avail_sp(sp_home or home["rotation"][0], home, rng)
    sp_away = _avail_sp(sp_away or away["rotation"][0], away, rng)
    staff = {"home": _Staff(home, sp_home), "away": _Staff(away, sp_away)}
    # Per-game injury availability: a short-IL regular sits this one (replacement
    # from the bench starts) with probability (1 - avail). Over the season this
    # reproduces the games they actually miss without deleting them outright.
    lineup, bench = {}, {}
    for side, prof in (("home", home), ("away", away)):
        lu, bn = list(prof["lineup"]), list(prof["bench"])
        for i, p in enumerate(lu):
            if p.get("avail", 1.0) < 1.0 and rng.random() > p["avail"] and bn:
                lu[i] = bn.pop(0)
        lineup[side], bench[side] = lu, bn
    idx = {"home": 0, "away": 0}
    used_ph = {"home": set(), "away": set()}
    bat_lines = {}
    score = {"home": 0, "away": 0}

    def bline(p):
        return bat_lines.setdefault(p["id"], _new_bat_line())

    inning = 1
    while True:
        for half in ("away", "home"):          # away bats first
            opp = "home" if half == "away" else "away"
            st = staff[opp]
            bases = [None, None, None]
            outs = 0
            while outs < 3:
                lead = score[opp] - score[half]
                st.maybe_hook(inning, lead)
                pit = st.cur
                slot = idx[half] % len(lineup[half])
                bat = lineup[half][slot]
                # Pinch-hit: late, close, weak spot due, better bench bat available.
                if (inning >= 7 and abs(lead) <= 3 and bench[half]):
                    ph = _pick_ph(bench[half], bat, pit["hand"], used_ph[half])
                    if ph:
                        lineup[half][slot] = ph
                        used_ph[half].add(ph["id"])
                        bench[half] = [b for b in bench[half] if b["id"] != ph["id"]]
                        bat = ph
                probs = _pa_probs(bat, pit)
                r = rng.random()
                cum = 0.0
                for k in ("k", "bb", "hbp", "hr", "1b", "2b", "3b"):
                    cum += probs[k]
                    if r < cum:
                        oc = k
                        break
                else:
                    oc = "out"
                bl = bline(bat); pl = st.lines[pit["id"]]
                bl["pa"] += 1; pl["bf"] += 1; st.outing_bf += 1
                if oc == "k":
                    bl["ab"] += 1; bl["k"] += 1; pl["k"] += 1; pl["outs"] += 1; outs += 1
                elif oc in ("bb", "hbp"):
                    if oc == "bb":
                        bl["bb"] += 1; pl["bb"] += 1
                    runs, scorers = _advance(bases, oc, bat, rng)
                    score[half] += runs; bl["rbi"] += runs
                    pl["r"] += runs; st.outing_runs += runs
                    for s in scorers:
                        bline(s)["r"] += 1
                elif oc == "out":
                    pl["outs"] += 1
                    # Productive out: with <2 outs, a runner on 3rd scores on a sac
                    # fly / grounder ~50% (not an at-bat), else a runner advances.
                    if outs < 2 and bases[2] and rng.random() < 0.50:
                        score[half] += 1; bl["rbi"] += 1; pl["r"] += 1
                        st.outing_runs += 1; bline(bases[2])["r"] += 1; bases[2] = None
                    else:
                        bl["ab"] += 1
                        if outs < 2 and bases[1] and not bases[2] and rng.random() < 0.30:
                            bases[2] = bases[1]; bases[1] = None   # grounder moves him up
                    outs += 1
                else:
                    bl["ab"] += 1; bl["h"] += 1; pl["h"] += 1
                    if oc == "2b":
                        bl["2b"] += 1
                    elif oc == "3b":
                        bl["3b"] += 1
                    elif oc == "hr":
                        bl["hr"] += 1; pl["hr"] += 1
                    runs, scorers = _advance(bases, oc, bat, rng)
                    score[half] += runs; bl["rbi"] += runs
                    pl["r"] += runs; st.outing_runs += runs
                    for s in scorers:
                        bline(s)["r"] += 1
                idx[half] += 1
        if inning >= 9 and score["home"] != score["away"]:
            break
        if inning >= 18:                       # safety cap on extra innings
            break
        inning += 1

    pit_lines = {}
    for side in ("home", "away"):
        pit_lines.update(staff[side].lines)
    return {"home_runs": score["home"], "away_runs": score["away"],
            "home_win": score["home"] > score["away"],
            "batting": bat_lines, "pitching": pit_lines,
            "innings": inning}
