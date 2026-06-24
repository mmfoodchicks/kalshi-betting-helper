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
    k = _log5(br["k"], pit["kpa"], LG["k"])
    bb = _log5(br["bb"], pit["bbpa"], LG["bb"])
    hr = _log5(br["hr"], pit["hrpa"], LG["hr"])
    hbp = br["hbp"]
    # Remaining mass goes to balls in play (hits + outs), tilted by the pitcher's
    # run prevention (better ERA suppresses hits a touch).
    rest = max(0.0, 1.0 - k - bb - hr - hbp)
    qual = max(0.7, min(1.3, 4.30 / max(2.0, pit["era"])))  # >1 = pitcher worse
    s, d, t = br["1b"], br["2b"], br["3b"]
    hit = (s + d + t) / qual            # better pitcher -> fewer hits in play
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
            tired = self.outing_bf >= 26 or (inning >= 6 and self.outing_bf >= 20)
            tagged = self.outing_runs >= 5 or (inning >= 6 and self.outing_runs >= 4)
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


def _advance(bases, outcome, rng):
    """Mutate bases for a hit/walk; return runs scored and RBIs credited."""
    runs = 0
    if outcome == "bb" or outcome == "hbp":
        if bases[0] and bases[1] and bases[2]:
            runs += 1                          # forced in
        elif bases[0] and bases[1]:
            bases[2] = True
        elif bases[0]:
            bases[1] = True
        bases[0] = True
        return runs
    if outcome == "hr":
        runs = 1 + sum(1 for b in bases if b)
        bases[0] = bases[1] = bases[2] = None
        return runs
    if outcome == "3b":
        runs = sum(1 for b in bases if b)
        bases[0] = bases[1] = None; bases[2] = True
        return runs
    if outcome == "2b":
        runs = (1 if bases[1] else 0) + (1 if bases[2] else 0)
        new = [None, True, None]               # batter to 2nd
        if bases[0]:
            new[2] = True                      # runner from 1st to 3rd
        bases[0], bases[1], bases[2] = new
        return runs
    # single: runner on 3rd scores; runner on 2nd scores ~55% else to 3rd;
    # runner on 1st to 2nd; batter to 1st.
    runs = 1 if bases[2] else 0
    new = [True, None, None]
    if bases[1]:
        if rng.random() < 0.55:
            runs += 1
        else:
            new[2] = True
    if bases[0]:
        new[1] = True
    bases[0], bases[1], bases[2] = new
    return runs


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


def play_game(home, away, sp_home=None, sp_away=None, rng=None):
    """Play one game. `home`/`away` are team_profiles; SPs default to rotation[0].
    Returns {home_runs, away_runs, home_win, batting:{pid:line}, pitching:{pid:line}}."""
    rng = rng or random
    sp_home = sp_home or home["rotation"][0]
    sp_away = sp_away or away["rotation"][0]
    staff = {"home": _Staff(home, sp_home), "away": _Staff(away, sp_away)}
    lineup = {"home": list(home["lineup"]), "away": list(away["lineup"])}
    bench = {"home": list(home["bench"]), "away": list(away["bench"])}
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
                    runs = _advance(bases, oc, rng)
                    score[half] += runs; bl["rbi"] += runs
                    pl["r"] += runs; st.outing_runs += runs
                elif oc == "out":
                    bl["ab"] += 1; pl["outs"] += 1; outs += 1
                else:
                    bl["ab"] += 1; bl["h"] += 1; pl["h"] += 1
                    if oc == "2b":
                        bl["2b"] += 1
                    elif oc == "3b":
                        bl["3b"] += 1
                    elif oc == "hr":
                        bl["hr"] += 1; pl["hr"] += 1
                    runs = _advance(bases, oc, rng)
                    score[half] += runs; bl["rbi"] += runs
                    pl["r"] += runs; st.outing_runs += runs
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
