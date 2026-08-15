"""DraftKings scoring, in ONE place, for every DFS sport we build.

Each sim used to carry its own copy of the rules, and copies drift. Auditing
them against DraftKings' published tables turned up real errors that had been
quietly shaping lineups:

  UFC     reversals/sweeps paid 3, DK pays 5 -- grapplers were under-projected.
  NFL     interceptions and lost fumbles cost 2, DK charges 1; and the 300/100/
          100-yard bonuses did not exist at all, so every workhorse back and
          every high-volume passer carried a systematically low ceiling.
  F1      the finish table was invented (43/40/38, then 41-pos) rather than DK's
          (40/37/35/32/30/...), the fastest lap paid 5 instead of 3, and laps led
          paid 0.1 instead of 0.25 -- which is 2.5x the entire dominator pool.
  NASCAR  the finish table was a straight 44-pos line, missing the two-point
          drops DK applies at 11th, 21st and 31st, so every mid-pack driver was
          scored up to 3 points rich.

So the tables live here and the sims import them. A number that appears on the
scoring card the user reads is the same number that scored the simulation.
"""

# ---- UFC (MMA) --------------------------------------------------------------
UFC = {
    "strike": 0.2,              # significant strike
    "control_sec": 0.03,        # per second of control time
    "takedown": 5,
    "reversal": 5,              # reversal / sweep
    "knockdown": 10,
    "win_round": {1: 90, 2: 70, 3: 45, 4: 40, 5: 40},
    "win_decision": 30,
    "quick_win": 25,            # first-round win in 60 seconds or less
}

# ---- MLB --------------------------------------------------------------------
MLB_HIT = {"single": 3, "double": 5, "triple": 8, "hr": 10, "rbi": 2, "run": 2,
           "bb": 2, "hbp": 2, "sb": 5}
MLB_PIT = {"out": 0.75, "k": 2, "win": 4, "er": -2, "hit": -0.6, "bb": -0.6,
           "hbp": -0.6, "cg": 2.5, "cg_shutout": 2.5, "no_hitter": 5}

# ---- NFL --------------------------------------------------------------------
NFL_OFF = {"pass_yd": 0.04, "pass_td": 4, "pass_300": 3, "int": -1,
           "rush_yd": 0.1, "rush_td": 6, "rush_100": 3,
           "rec": 1, "rec_yd": 0.1, "rec_td": 6, "rec_100": 3,
           "return_td": 6, "fumble_lost": -1, "two_point": 2,
           "fum_rec_td": 6}
NFL_DST = {"sack": 1, "int": 2, "fumble_rec": 2, "return_td": 6, "int_td": 6,
           "fum_rec_td": 6, "blocked_return_td": 6, "safety": 2,
           "blocked_kick": 2, "two_point_return": 2}
# Points allowed -> DST points. Ordered, first match wins.
NFL_DST_PA = [(0, 0, 10), (1, 6, 7), (7, 13, 4), (14, 20, 1),
              (21, 27, 0), (28, 34, -1), (35, 999, -4)]


def nfl_dst_pa_points(pa):
    for lo, hi, pts in NFL_DST_PA:
        if lo <= pa <= hi:
            return pts
    return -4


# ---- NASCAR -----------------------------------------------------------------
# DK's table is NOT a straight line: it steps down an extra point at 11th, 21st
# and 31st. Modelling it as 44-pos scored every mid-pack driver rich.
NASCAR_FINISH = {
    1: 45, 2: 42, 3: 41, 4: 40, 5: 39, 6: 38, 7: 37, 8: 36, 9: 35, 10: 34,
    11: 32, 12: 31, 13: 30, 14: 29, 15: 28, 16: 27, 17: 26, 18: 25, 19: 24,
    20: 23, 21: 21, 22: 20, 23: 19, 24: 18, 25: 17, 26: 16, 27: 15, 28: 14,
    29: 13, 30: 12, 31: 10, 32: 9, 33: 8, 34: 7, 35: 6, 36: 5, 37: 4, 38: 3,
    39: 2, 40: 1,
}
NASCAR = {"place_differential": 1, "fastest_lap": 0.45, "lap_led": 0.25}


def nascar_finish(pos):
    """DK finishing points for a NASCAR position (beyond 40th scores 0)."""
    try:
        pos = int(pos)
    except (TypeError, ValueError):
        return 0.0
    if pos < 1:
        return float(NASCAR_FINISH[1])
    return float(NASCAR_FINISH.get(pos, 0))


# ---- F1 ---------------------------------------------------------------------
F1_FINISH = {
    1: 40, 2: 37, 3: 35, 4: 32, 5: 30, 6: 27, 7: 25, 8: 23, 9: 22, 10: 20,
    11: 17, 12: 15, 13: 13, 14: 12, 15: 10, 16: 7, 17: 5, 18: 4, 19: 3,
    20: 2, 21: 1, 22: 0,
}
F1 = {"place_differential": 1, "fastest_lap": 3, "lap_led": 0.25,
      "defeated_teammate": 5, "classified": 1}


def f1_finish(pos):
    """DK finishing points for an F1 position (beyond 22nd scores 0)."""
    try:
        pos = int(pos)
    except (TypeError, ValueError):
        return 0.0
    if pos < 1:
        return float(F1_FINISH[1])
    return float(F1_FINISH.get(pos, 0))


# ---- The card the app shows -------------------------------------------------
# Rendered straight from the constants above, so the card cannot disagree with
# the simulation. Each row is (label, value-text, optional note).
def _pos_rows(table, top=10):
    rows = [(f"{p}{'st' if p == 1 else 'nd' if p == 2 else 'rd' if p == 3 else 'th'} place",
             f"+{table[p]:g} Pts") for p in sorted(table)[:top]]
    last = max(table)
    rows.append((f"11th–{last}th", f"+{table[11]:g} down to +{table[last]:g} Pts"))
    return rows


def card(sport):
    """The DK scoring card for a sport: [{"group": .., "rows": [(label, val)]}]."""
    s = (sport or "").lower()
    if s in ("ufc", "mma"):
        w = UFC["win_round"]
        return [
            {"group": "Moves", "rows": [
                ("Significant Strikes", f"+{UFC['strike']} Pts"),
                ("Control Time", f"+{UFC['control_sec']} Pts/Second"),
                ("Takedown", f"+{UFC['takedown']} Pts"),
                ("Reversal/Sweep", f"+{UFC['reversal']} Pts"),
                ("Knockdown", f"+{UFC['knockdown']} Pts")]},
            {"group": "Fight Conclusion Bonuses", "rows": [
                ("1st Round Win", f"+{w[1]} Pts"), ("2nd Round Win", f"+{w[2]} Pts"),
                ("3rd Round Win", f"+{w[3]} Pts"), ("4th Round Win", f"+{w[4]} Pts"),
                ("5th Round Win", f"+{w[5]} Pts"),
                ("Decision Win", f"+{UFC['win_decision']} Pts"),
                ("Quick Win Bonus (R1 ≤60s)", f"+{UFC['quick_win']} Pts")]}]
    if s in ("mlb", "baseball"):
        h, p = MLB_HIT, MLB_PIT
        return [
            {"group": "Hitters", "rows": [
                ("Single", f"+{h['single']} Pts"), ("Double", f"+{h['double']} Pts"),
                ("Triple", f"+{h['triple']} Pts"), ("Home Run", f"+{h['hr']} Pts"),
                ("Run Batted In", f"+{h['rbi']} Pts"), ("Run", f"+{h['run']} Pts"),
                ("Base on Balls", f"+{h['bb']} Pts"), ("Hit By Pitch", f"+{h['hbp']} Pts"),
                ("Stolen Base", f"+{h['sb']} Pts")]},
            {"group": "Pitchers", "rows": [
                ("Inning Pitched", f"+{p['out'] * 3:g} Pts (+{p['out']:g}/Out)"),
                ("Strikeout", f"+{p['k']} Pts"), ("Win", f"+{p['win']} Pts"),
                ("Earned Run Allowed", f"{p['er']} Pts"),
                ("Hit Against", f"{p['hit']} Pts"),
                ("Base on Balls Against", f"{p['bb']} Pts"),
                ("Hit Batsman", f"{p['hbp']} Pts"),
                ("Complete Game", f"+{p['cg']} Pts"),
                ("Complete Game Shutout", f"+{p['cg_shutout']} Pts"),
                ("No Hitter", f"+{p['no_hitter']} Pts")]}]
    if s in ("nfl", "football"):
        o, d = NFL_OFF, NFL_DST
        return [
            {"group": "Offense", "rows": [
                ("Passing TD", f"+{o['pass_td']} Pts"),
                ("25 Passing Yards", f"+1 Pt (+{o['pass_yd']}/Yard)"),
                ("300+ Yard Passing Game", f"+{o['pass_300']} Pts"),
                ("Interception", f"{o['int']} Pt"),
                ("Rushing TD", f"+{o['rush_td']} Pts"),
                ("10 Rushing Yards", f"+1 Pt (+{o['rush_yd']}/Yard)"),
                ("100+ Yard Rushing Game", f"+{o['rush_100']} Pts"),
                ("Receiving TD", f"+{o['rec_td']} Pts"),
                ("10 Receiving Yards", f"+1 Pt (+{o['rec_yd']}/Yard)"),
                ("100+ Receiving Yard Game", f"+{o['rec_100']} Pts"),
                ("Reception", f"+{o['rec']} Pt"),
                ("Punt/Kickoff/FG Return TD", f"+{o['return_td']} Pts"),
                ("Fumble Lost", f"{o['fumble_lost']} Pt"),
                ("2 Pt Conversion", f"+{o['two_point']} Pts")]},
            {"group": "Defense / Special Teams", "rows": [
                ("Sack", f"+{d['sack']} Pt"), ("Interception", f"+{d['int']} Pts"),
                ("Fumble Recovery", f"+{d['fumble_rec']} Pts"),
                ("Any Defensive/Return TD", f"+{d['int_td']} Pts"),
                ("Safety", f"+{d['safety']} Pts"),
                ("Blocked Kick", f"+{d['blocked_kick']} Pts")]},
            {"group": "DST Points Allowed", "rows": [
                ("0 Points", "+10 Pts"), ("1–6", "+7 Pts"), ("7–13", "+4 Pts"),
                ("14–20", "+1 Pt"), ("21–27", "+0 Pts"), ("28–34", "-1 Pt"),
                ("35+", "-4 Pts")]}]
    if s == "nascar":
        return [
            {"group": "General", "rows": [
                ("Place Differential", f"+/- {NASCAR['place_differential']} Pt"),
                ("Fastest Laps", f"+{NASCAR['fastest_lap']} Pts"),
                ("Laps Led", f"+{NASCAR['lap_led']} Pts")]},
            {"group": "Finishing Position", "rows": _pos_rows(NASCAR_FINISH)}]
    if s == "f1":
        return [
            {"group": "General", "rows": [
                ("Place Differential", f"+/- {F1['place_differential']} Pt"),
                ("Fastest Lap (of race)", f"+{F1['fastest_lap']} Pts"),
                ("Laps Led", f"+{F1['lap_led']} Pts"),
                ("Defeated Teammate", f"+{F1['defeated_teammate']} Pts"),
                ("Classified at Finish", f"+{F1['classified']} Pt")]},
            {"group": "Finishing Position", "rows": _pos_rows(F1_FINISH)}]
    return []
