"""Drive-level NFL game engine — the football twin of the MLB deep sim.

Each simulated game walks alternating DRIVES. A drive resolves TD / FG / punt /
turnover from per-drive rates derived from the matchup-adjusted expected team
production (nfl_data: Sleeper player sums + kickers). The engine layers on the
dynamics that make football football:

  - GAME SCRIPT: in the 4th quarter a team down more than one score goes
    hurry-up (extra possessions, pass tilt, more picks); a team protecting a
    lead runs and kneels (fewer possessions, run tilt). The tilt is tracked and
    later shapes every player's stat line, so a losing team's WR gets his
    garbage-time yards and a winning team's RB eats.
  - SHORT FIELDS: a turnover hands the next drive a boosted scoring chance.
  - OT on a tie (no ties in the output — Kalshi MLs resolve a winner).

After the drives settle the team totals, the engine deals each team's simulated
passing/rushing game back out to the PLAYERS by their share of the team's
expected production (with per-player volume noise), and TDs multinomially by
each player's TD share. Every player line is therefore consistent with the team
total, the final score and the script — which is exactly what makes same-game
parlays honest. Leg masks feed the same bitmask machinery MLB uses
(mlb_sim.best_same_game / game_bundles), so NFL gets correlated SGPs for free.
"""
import math
import predlog
import random
import errlog

# ---- Engine constants -------------------------------------------------------
_DRIVES = 10.7            # nominal possessions per team per game
# Home field, fitted to the MEASURED home win rate. Sleeper's projections carry
# ZERO venue signal (same team, home weeks minus away weeks, 32 teams over four
# 2026 weeks: -0.07 +/- 0.27 points), so whatever home edge the board shows has
# to come from the engine -- and the old one-sided 1.03 bump realized +0.20
# points against a real +2.13 (2023-25, n=816), underpricing every home side by
# ~2 points. The bump is now SPLIT (home x1.05, visitor /1.05) so the total
# stays put, and 1.05 is fitted so equal teams give p_home 0.5415 against the
# real 0.5423 home win rate. That realizes +1.5 points of margin, not +2.13,
# deliberately: real margins are right-skewed by blowouts, so matching the mean
# would overshoot the win rate by ~2pp -- and the moneyline is the market that
# gets logged, calibrated and built into combos, while spreads price off the
# center of the distribution, not its skewed mean.
_HFA_SCORE = 1.05
# Structural calibration (measured over a full slate): short fields, hurry-up
# possessions and OT add ~3.3% points beyond the per-drive rates, so the rates
# are trimmed to land the simulated total on the expected total.
_CAL = 0.968
_SHORT_FIELD = 1.35       # scoring-rate boost on the drive after a takeaway
_XP_MAKE = 0.945
_TWO_PT = 0.06            # share of TDs that go for 2 (converts ~48%)
_HURRY_TILT = 0.30        # Q4 trailing pass tilt (leader gets the mirror run tilt)
_YD_SD = 0.16             # team yardage noise beyond volume/script effects
_PLAYER_SD = 0.28         # per-player volume noise around his team share
_FORM_SD = 0.20           # zero-sum game-control tilt (see _play_game); fitted
                          # so the engine's margin SD lands on the measured 13.3
_FORM_SD_PRE = 0.26       # preseason tilt: roster churn scatters results wider
                          # at a LOWER scoring level (real margin SD 13.70 on a
                          # 38.6 total against 14.37 on 45.2; n=147/801 2023-25),
                          # so the same 13.5-ish conditional SD needs more tilt


def _pois(lam, rng):
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    L = math.exp(-lam); k = 0; p = 1.0
    while True:
        k += 1; p *= rng.random()
        if p <= L:
            return k - 1


def _shock(rng, logsd=None):
    """Per-player volume multiplier for one game.

    `logsd` set (preseason) draws a MEAN-PRESERVING lognormal: subtracting
    sigma^2/2 from the mean of the log is what keeps E[mult] at 1.0, so widening
    a player's distribution does not quietly raise his projection -- the whole
    point is to move probability into both tails, not to move the line."""
    if logsd:
        return math.exp(rng.gauss(0.0, logsd) - logsd * logsd / 2.0)
    return max(0.1, rng.gauss(1.0, _PLAYER_SD))


# WHO YOU PLAY. The per-drive rates are built from Sleeper's weekly projections,
# which are matchup-aware in principle -- and barely in practice. Measured on the
# 2025 projections, the within-team slope of a club's projected points against
# its opponent's points allowed per game is +0.124 (n=416): Sleeper moves a
# team's number by under two points across the league's whole defensive range.
#
# What the right slope is, measured on three seasons of real results (n=1,708,
# leave-one-out season rates so no game sees itself, log-log, controlling for the
# team's own offence):
#
#     own-offence exponent      +0.697
#     OPPONENT-DEFENCE exponent +0.349 +/- 0.087   t = +4.03
#
# Real, and much smaller than proportional -- points allowed is a noisy read on a
# defence, so it regresses hard. The engine's job is the part Sleeper has NOT
# already priced, so it applies the RESIDUAL exponent and nothing more; using the
# full 0.349 would count the first 0.124 twice.
_DEF_EXP = 0.225
_DEF_CLAMP = (0.88, 1.14)


def def_factor(opp_pa_pg, lg_pa_pg):
    """Scoring multiplier from the defence a team is facing (weak D -> more points)."""
    if not opp_pa_pg or not lg_pa_pg or opp_pa_pg <= 0 or lg_pa_pg <= 0:
        return 1.0
    lo, hi = _DEF_CLAMP
    return max(lo, min(hi, (opp_pa_pg / lg_pa_pg) ** _DEF_EXP))


def _rates(prof, home, def_mult=1.0):
    """Per-drive outcome rates from a team's expected production, against the
    defence it is actually facing."""
    e = prof["exp"]
    tds = e["pass_td"] + e["rush_td"]
    hfa = _HFA_SCORE if home else 1.0 / _HFA_SCORE
    p_td = max(0.04, min(0.55, tds / _DRIVES * hfa * _CAL * def_mult))
    p_fg = max(0.02, min(0.40, e["fgm"] / _DRIVES * hfa * _CAL * def_mult))
    # Takeaways belong to the DEFENCE forcing them, so a good defence raises the
    # offence's turnover rate — the multiplier goes the other way here.
    p_to = max(0.02, min(0.30, (e["pass_int"] + e["fum_lost"]) / _DRIVES / def_mult))
    return p_td, p_fg, p_to


def _drive(p_td, p_fg, p_to, boost, rng):
    """One drive -> ('td'|'fg'|'to'|'punt')."""
    r = rng.random()
    td, fg = p_td * boost, p_fg * min(boost, 1.2)
    if r < td:
        return "td"
    if r < td + fg:
        return "fg"
    if r < td + fg + p_to:
        return "to"
    return "punt"


def _play_game(rh, ra, rng, form_sd=None):
    """One simulated game. Returns (pts_h, pts_a, stats) where stats carries each
    side's realized TD/FG counts, possession count and pass/run tilt.

    `form_sd` (default _FORM_SD) draws a ZERO-SUM game-control tilt: one team's
    good day is, in part, the other team's bad day -- the line play, the field
    position and the clock are all adversarial. Playing every simulated game at
    the same rates left all variance to drive-level dice, and the measured
    margin SD came out 10.90 against a real 13.3 (conditional; 2023-25, n=801,
    unconditional 14.37 less a ~5.4-SD spread distribution) while the total SD
    was already right -- so the missing variance is ANTI-correlated between the
    sides, and a symmetric widening would have ruined totals to fix margins.
    The tilt multiplies each side's TD/FG rates by e^{+/-g}, mean-preserving,
    adding variance to the margin and, linearized, none to the total."""
    if form_sd is None:
        form_sd = _FORM_SD
    if form_sd:
        g_ = rng.gauss(0.0, form_sd)
        th = math.exp(g_ - form_sd * form_sd / 2.0)
        ta = math.exp(-g_ - form_sd * form_sd / 2.0)
        rh = (rh[0] * th, rh[1] * th, rh[2])
        ra = (ra[0] * ta, ra[1] * ta, ra[2])
        # keep each side a proper probability row even at extreme draws
        for r_ in (rh, ra):
            s_ = r_[0] + r_[1] + r_[2]
            if s_ > 0.95:
                rh_is = r_ is rh
                r_ = (r_[0] * 0.95 / s_, r_[1] * 0.95 / s_, r_[2] * 0.95 / s_)
                if rh_is:
                    rh = r_
                else:
                    ra = r_
    sides = ({"r": rh, "pts": 0, "td": 0, "fg": 0, "drv": 0, "tilt": 0.0},
             {"r": ra, "pts": 0, "td": 0, "fg": 0, "drv": 0, "tilt": 0.0})

    def score_td(s):
        s["pts"] += 6
        if rng.random() < _TWO_PT:
            s["pts"] += 2 if rng.random() < 0.48 else 0
        elif rng.random() < _XP_MAKE:
            s["pts"] += 1

    n_pairs = 11
    boost = [1.0, 1.0]                  # short-field carryover per side
    extra = [0, 0]                      # hurry-up (+1) / kneel (-1) possessions
    for i in range(n_pairs):
        q4 = i >= 8                     # last stretch = the 4th quarter
        for si in (0, 1):
            s, o = sides[si], sides[1 - si]
            if q4:                      # script: set tilt + possession deltas once
                diff = s["pts"] - o["pts"]
                if diff < -8:
                    s["tilt"] = _HURRY_TILT; extra[si] = 1
                elif diff > 8:
                    s["tilt"] = -_HURRY_TILT; extra[si] = -1
            if i == n_pairs - 1 and extra[si] < 0:
                continue                # kneel-out: leader skips the final drive
            td, fg, to = s["r"]
            if q4 and s["tilt"] > 0:    # hurry-up presses: more TDs, more picks
                td *= 1.10; to *= 1.35
            elif q4 and s["tilt"] < 0:
                td *= 0.88              # clock-killing ground game
            out = _drive(td, fg, to, boost[si], rng)
            s["drv"] += 1
            boost[si] = 1.0
            if out == "td":
                s["td"] += 1; score_td(s)
            elif out == "fg":
                s["fg"] += 1; s["pts"] += 3
            elif out == "to":
                boost[1 - si] = _SHORT_FIELD
        if q4 and i == n_pairs - 1:     # hurry-up buys one more possession
            for si in (0, 1):
                if extra[si] > 0:
                    s = sides[si]
                    td, fg, to = s["r"]
                    out = _drive(td * 1.10, fg, to * 1.35, boost[si], rng)
                    s["drv"] += 1
                    if out == "td":
                        s["td"] += 1; score_td(s)
                    elif out == "fg":
                        s["fg"] += 1; s["pts"] += 3

    if sides[0]["pts"] == sides[1]["pts"]:          # overtime, one round each
        for si in (0, 1):
            s = sides[si]
            out = _drive(*s["r"], 1.0, rng)
            if out == "td":
                s["td"] += 1; s["pts"] += 7
            elif out == "fg":
                s["fg"] += 1; s["pts"] += 3
        if sides[0]["pts"] == sides[1]["pts"]:      # still tied: strength coin
            w = sides[0] if rng.random() < rh[0] / (rh[0] + ra[0]) else sides[1]
            w["pts"] += 3
    return sides


# ---- Player allocation ------------------------------------------------------
def _shares(prof):
    """Normalized usage shares per category + per-player expected lines."""
    ps = prof["players"]
    tot = {k: sum(p[k] for p in ps) or 1.0
           for k in ("pass_yd", "rush_yd", "rec_yd", "rec", "pass_td", "rush_td", "rec_td")}
    return ps, tot


def simulate_game(home, away, n=2400, seed=None, ladders=None, prop_lad=None,
                  shock=None):
    """Correlated Monte Carlo of one game from two nfl_data team profiles.

    Returns win probs, score/margin/total distributions, per-player prop
    probabilities (yardage ladders, receptions, anytime TD) and the bitmask
    candidate legs the MLB parlay machinery consumes."""
    rng = random.Random(seed if seed is not None
                        else hash((home["abbr"], away["abbr"])) & 0xFFFFFFFF)
    # Each side is rated against the OTHER side's defence. `def_pa_pg` is attached
    # by the board build; without it both multipliers are 1.0 and the engine
    # behaves exactly as it did before.
    lg_pa = home.get("lg_pa_pg") or away.get("lg_pa_pg")
    dh = def_factor(away.get("def_pa_pg"), lg_pa)      # home offence vs away defence
    da = def_factor(home.get("def_pa_pg"), lg_pa)
    rh = _rates(home, home.get("home") is not False, dh)
    ra = _rates(away, False, da)
    hp, ht = _shares(home)
    ap, at = _shares(away)

    n_ply = len(hp) + len(ap)
    hw = 0
    margins, totals = [], []
    pts_h = [0.0] * n
    pts_a = [0.0] * n
    # per-player per-sim stat lines (yards floats; tds ints)
    lines = [{"pass_yd": [0.0] * n, "rush_yd": [0.0] * n, "rec_yd": [0.0] * n,
              "rec": [0.0] * n, "td": [0] * n, "pass_td": [0] * n}
             for _ in range(n_ply)]

    for s in range(n):
        # `shock` set means the preseason path -- wider game-control tilt there.
        g = _play_game(rh, ra, rng, form_sd=_FORM_SD_PRE if shock else None)
        ph, pa = g[0]["pts"], g[1]["pts"]
        if ph > pa:
            hw += 1
        margins.append(ph - pa)
        totals.append(ph + pa)
        pts_h[s], pts_a[s] = ph, pa

        off = 0
        for side, (ps, tot), prof in ((g[0], (hp, ht), home), (g[1], (ap, at), away)):
            e = prof["exp"]
            vol = side["drv"] / _DRIVES
            tilt = side["tilt"]
            pass_mult = vol * (1 + 0.45 * tilt) * max(0.3, rng.gauss(1.0, _YD_SD))
            rush_mult = vol * (1 - 0.55 * tilt) * max(0.3, rng.gauss(1.0, _YD_SD))
            team_pass = e["pass_yd"] * pass_mult
            team_rush = e["rush_yd"] * rush_mult
            # deal realized TDs to players by TD share (pass TDs = catchers' TDs)
            n_td = side["td"]
            pass_td_n = sum(1 for _ in range(n_td)
                            if rng.random() < e["pass_td"] / max(0.1, e["pass_td"] + e["rush_td"]))
            rush_td_n = n_td - pass_td_n

            def deal(count, share_key, tot_key):
                out = [0] * len(ps)
                denom = tot[tot_key]
                for _ in range(count):
                    r = rng.random() * denom
                    acc = 0.0
                    for i, p in enumerate(ps):
                        acc += p[share_key]
                        if r < acc:
                            out[i] += 1
                            break
                return out
            rec_tds = deal(pass_td_n, "rec_td", "rec_td")
            rush_tds = deal(rush_td_n, "rush_td", "rush_td")

            for i, p in enumerate(ps):
                L = lines[off + i]
                noise = _shock(rng, shock)
                if p["pass_yd"] > 0:
                    # A quarterback's yardage tracks his team's passing game
                    # almost exactly in the regular season -- he is the only one
                    # throwing. In an exhibition it does not: whether he plays a
                    # series or a half is a coaching decision, so the preseason
                    # shock applies to him too.
                    qn = _shock(rng, shock) if shock else max(0.5, rng.gauss(1.0, 0.08))
                    L["pass_yd"][s] = p["pass_yd"] / (tot["pass_yd"] or 1) * team_pass * qn
                    L["pass_td"][s] = pass_td_n if p["pass_yd"] / tot["pass_yd"] > 0.7 \
                        else _pois(p["pass_td"] * pass_mult, rng)
                if p["rush_yd"] > 0:
                    L["rush_yd"][s] = p["rush_yd"] / tot["rush_yd"] * team_rush * noise
                if p["rec_yd"] > 0:
                    rn = _shock(rng, shock)
                    L["rec_yd"][s] = p["rec_yd"] / tot["rec_yd"] * team_pass \
                        * (tot["rec_yd"] / (tot["pass_yd"] or 1)) * rn
                    L["rec"][s] = p["rec"] / (tot["rec"] or 1) * (e["rec"] * pass_mult) * rn
                L["td"][s] = rec_tds[i] + rush_tds[i]
            off += len(ps)

    all_ps = list(hp) + list(ap)
    team_of = [home["abbr"]] * len(hp) + [away["abbr"]] * len(ap)

    def q(arr, f):
        s2 = sorted(arr)
        return s2[min(len(s2) - 1, int(f * len(s2)))]

    p_home = hw / n
    exp_h, exp_a = sum(pts_h) / n, sum(pts_a) / n
    mean_total = sum(totals) / n

    # Ladders off the sim distributions.
    total_ladder = []
    base = round(mean_total)
    for line in [base + d + 0.5 for d in range(-8, 9)]:
        over = sum(1 for t in totals if t > line) / n
        if 0.04 <= over <= 0.96:
            total_ladder.append({"line": line, "over_pct": round(over * 100, 1),
                                 "under_pct": round((1 - over) * 100, 1)})
    spread_ladder = {"home": {}, "away": {}}
    for m in (1, 3, 4, 7, 10, 14):
        spread_ladder["home"][str(m)] = round(100 * sum(1 for x in margins if x >= m) / n, 1)
        spread_ladder["away"][str(m)] = round(100 * sum(1 for x in margins if x <= -m) / n, 1)

    # Player boards + prop ladders. The cutoff for "worth showing" is a fraction
    # of a real game in the preseason: a team runs for 109 yards a side in an
    # exhibition against ~120 in September, split across five backs instead of
    # two, so a 25-yard floor built for the regular season hides the ENTIRE
    # running back and receiver board -- which is exactly the board Kalshi books.
    floor_yd, floor_rec = (10.0, 1.0) if shock else (25.0, 2.5)
    players, props = [], []
    for i, p in enumerate(all_ps):
        L = lines[i]
        row = {"name": p["name"], "pos": p["pos"], "team": team_of[i]}
        for key, lab in (("pass_yd", "pass yds"), ("rush_yd", "rush yds"),
                         ("rec_yd", "rec yds"), ("rec", "receptions")):
            arr = L[key]
            mean = sum(arr) / n
            if mean < (floor_rec if key == "rec" else floor_yd):
                continue
            row[key] = round(mean, 1)
            row[key + "_floor"] = round(q(arr, 0.15), 1)
            row[key + "_ceil"] = round(q(arr, 0.85), 1)
            step = 1 if key == "rec" else 5
            lo = math.floor(mean * 0.7 / step) * step
            for line in [lo + step * j + 0.5 for j in range(0, 7)]:
                over = sum(1 for x in arr if x > line) / n
                if 0.10 <= over <= 0.92:
                    props.append({"player": p["name"], "pos": p["pos"],
                                  "team": team_of[i], "stat": lab,
                                  "line": line, "over_pct": round(over * 100, 1)})
        td1 = sum(1 for x in L["td"] if x >= 1) / n
        if td1 >= (0.05 if shock else 0.12):
            row["td1_pct"] = round(td1 * 100, 1)
            props.append({"player": p["name"], "pos": p["pos"], "team": team_of[i],
                          "stat": "anytime TD", "line": 0.5,
                          "over_pct": round(td1 * 100, 1)})
        if len(row) > 3:
            players.append(row)

    return {
        "home": home["abbr"], "away": away["abbr"],
        "home_name": home.get("name"), "away_name": away.get("name"),
        "date": home.get("date"), "state": home.get("state"),
        "p_home": round(p_home, 4), "p_away": round(1 - p_home, 4),
        "exp_home": round(exp_h, 1), "exp_away": round(exp_a, 1),
        "exp_total": round(mean_total, 1),
        "mean_margin": round(sum(margins) / n, 1),
        "total_ladder": total_ladder, "spread_ladder": spread_ladder,
        "players": players, "props": props, "n_sims": n,
        "_masks": _build_masks(home, away, hp, ap, lines, margins, totals,
                               p_home, n, ladders=ladders, prop_lad=prop_lad),
    }


# ---- Bitmask candidate legs (feeds mlb_sim's SGP machinery) -----------------
def _mask_of(pred, n):
    m = 0
    for i in range(n):
        if pred(i):
            m |= (1 << i)
    return m


def _build_masks(home, away, hp, ap, lines, margins, totals, p_home, n,
                 ladders=None, prop_lad=None):
    """Candidate legs with sim masks. Shape matches mlb_sim's candidates so
    best_same_game / game_bundles run unchanged."""
    cands = []

    def add(typ, label, mask, group, kref=None, avg=None, unit=None):
        marg = bin(mask).count("1") / n
        if 0.05 <= marg <= 0.96:
            cands.append({"type": typ, "label": label, "mask": mask, "marg": marg,
                          "group": group, "model_pct": None, "kref": kref,
                          "sim_avg": avg, "avg_unit": unit})

    h, a = home["abbr"], away["abbr"]
    add("ML", f"{home.get('name', h)} to win", _mask_of(lambda i: margins[i] > 0, n),
        "ML", kref={"t": "ml", "team": h}, avg=round(sum(margins) / n, 1), unit="pt margin")
    add("ML", f"{away.get('name', a)} to win", _mask_of(lambda i: margins[i] < 0, n),
        "ML", kref={"t": "ml", "team": a}, avg=round(-sum(margins) / n, 1), unit="pt margin")
    mean_total = sum(totals) / n
    # Spread and total legs come off KALSHI'S OWN LADDER when we have it, with the
    # kref that prices them. These used to be three hardcoded margins and three
    # made-up totals carrying no kref at all, so every one of them went out
    # unpriced against a market that books two dozen lines a game -- and a combo
    # cannot walk to the line that lands in a confidence band if the only lines it
    # knows about are the ones it invented.
    sp = (ladders or {}).get("spread") or {}
    margins_h = sp.get(h) or [3, 7, 10]
    margins_a = sp.get(a) or [3, 7, 10]
    import combo_engine as _ce
    for m in margins_h:
        add("Spread", _ce.spread_label(h, m, "points"),
            _mask_of(lambda i, m=m: margins[i] >= m, n), "Spread",
            kref={"t": "spread", "team": h, "by": m},
            avg=round(sum(margins) / n, 1), unit="pt margin")
    for m in margins_a:
        add("Spread", _ce.spread_label(a, m, "points"),
            _mask_of(lambda i, m=m: margins[i] <= -m, n), "Spread",
            kref={"t": "spread", "team": a, "by": m},
            avg=round(-sum(margins) / n, 1), unit="pt margin")
    tl = (ladders or {}).get("total")
    lines_t = tl if tl else [round(mean_total) + d for d in (-4, 0, 4)]
    for nn in lines_t:
        line = nn - 0.5
        add("Total", f"Over {line}", _mask_of(lambda i, L=line: totals[i] > L, n),
            "Total", kref={"t": "total", "n": nn, "over": True},
            avg=round(mean_total, 1), unit="points")
        add("Total", f"Under {line}", _mask_of(lambda i, L=line: totals[i] < L, n),
            "Total", kref={"t": "total", "n": nn, "over": False},
            avg=round(mean_total, 1), unit="points")

    # Player legs come off KALSHI'S OWN LADDER wherever it books one -- with the
    # kref that prices them -- and fall back to an invented line only where it
    # does not. Preseason props exist and are traded (Carson Beck has four rungs
    # with real volume), so a made-up line here would throw away both the price
    # and the market's read on who is even playing.
    prop_lad = prop_lad or {}
    all_ps = list(hp) + list(ap)
    for i, p in enumerate(all_ps):
        L = lines[i]
        nm = p["name"]
        key_nm = _nkey(nm)
        for key, lab, step in (("pass_yd", "pass yds", 25), ("rush_yd", "rush yds", 15),
                               ("rec_yd", "rec yds", 15), ("rec", "receptions", 1)):
            arr = L[key]
            mean = sum(arr) / n
            booked = (prop_lad.get((key, key_nm)) or {}).get("rungs") or []
            if booked:
                for ln, _p in booked:
                    add(lab.title(), f"{nm} {ln}+ {lab}",
                        _mask_of(lambda i2, A=arr, l2=ln: A[i2] > l2, n),
                        f"{nm}:{key}", avg=round(mean, 1), unit=lab,
                        kref={"t": "prop", "stat": key, "player": nm, "line": ln})
                continue
            if mean < (3 if key == "rec" else 30):
                continue
            line = (math.floor(mean / step) * step) + 0.5
            add(lab.title(), f"{nm} {line}+ {lab}",
                _mask_of(lambda i2, A=arr, ln=line: A[i2] > ln, n),
                f"{nm}:{key}", avg=round(mean, 1), unit=lab)
        td_booked = (prop_lad.get(("td", key_nm)) or {}).get("rungs") or []
        if td_booked:
            for ln, _p in td_booked:
                k = int(math.ceil(ln))
                add("TD", f"{nm} {k}+ TD",
                    _mask_of(lambda i2, A=L["td"], k2=k: A[i2] >= k2, n),
                    f"{nm}:td",
                    kref={"t": "prop", "stat": "td", "player": nm, "line": ln})
        elif sum(1 for x in L["td"] if x >= 1) / n >= 0.15:
            add("TD", f"{nm} anytime TD",
                _mask_of(lambda i2, A=L["td"]: A[i2] >= 1, n), f"{nm}:td")
    return cands


def same_game_parlay(sim, n_legs=3, target=0.5, target_payout=None, max_legs=5):
    """Best correlated same-game parlay for one simulated game, via the shared
    MLB bitmask machinery."""
    import mlb_sim
    return mlb_sim.best_same_game(sim["_masks"], sim["n_sims"], n_legs, target,
                                  target_payout or 0, max_legs)


# ---- Weekly slate board -----------------------------------------------------
import threading as _threading
import time as _time

_cache = {}
_inflight = set()


def _season():
    import clock
    t = clock.today_et()
    return t.year if t.month >= 3 else t.year - 1


def current_week(preseason=False):
    """The week the NFL tab should open on: the first whose games are not all in
    the past (ET). The tab used to default to week 1 forever, which is right for
    a few days a year — the morning after the Hall of Fame game it served one
    already-played exhibition while sixteen games sat under week 2, and the
    board read "no games" until the user guessed the dropdown."""
    import clock
    import nfl_live
    today = clock.today_et().isoformat()
    last = 4 if preseason else 18
    for wk in range(1, last + 1):
        try:
            sch = nfl_live.schedule(wk, int(_season()),
                                    seasontype=1 if preseason else 2) or []
        except Exception:
            sch = []
        if any((g.get("date") or "")[:10] >= today for g in sch):
            return wk
    return last


# --- PRESEASON ----------------------------------------------------------------
# An exhibition has no usable team profile: Sleeper projects nothing in August
# (every field comes back null), so there is no expected production to hand the
# drive engine. Kalshi, however, books a deep ladder on these games -- 24 spread
# markets and 19 totals on the Hall of Fame game -- and a ladder IS an estimate
# of the score.
#
# So preseason games are simulated MARKET-ANCHORED: the level comes from the
# de-vigged ladder, and the engine supplies the JOINT structure a parlay needs
# and a set of independent market prices cannot. That division is the honest one.
# It does not claim an edge on the level -- it claims that the market prices each
# line separately and does not price the correlation between them.
# The PLAYERS come from nfl_preseason, which measured what an exhibition team-game
# actually looks like off Sleeper's preseason STATS feed (the projections feed is
# null in August, the stats feed is not) and distributes it by the inverted usage
# model -- the third quarterback throws more than the first. Where Kalshi books a
# player, his ladder overrides the model for that stat: the market prices THIS
# player in THIS exhibition, which no positional model can match.
#
# The drive engine realizes slightly less than the points it is asked for --
# stable across the range, so it is a bias and not noise, and it is divided out
# rather than left for the caller to wonder about. REMEASURED after the
# game-control tilt and the split home bump landed (the split is x1.05 up and
# /1.05 down, which nets a hair under 1.0, and the tilt's clamps shave a little
# more), raw response under preseason conditions:
#
#     asked   30.0   35.0   40.0   46.0   52.0
#     got     28.8   33.6   38.4   44.4   50.4
#     ratio  0.960  0.960  0.960  0.965  0.969
#
# Flat 0.960 through the preseason's working range (36-44 totals), drifting
# toward 0.969 only at totals August never sees; 0.961 splits the difference
# where the games actually live.
_ENGINE_BIAS = 0.961

# What the engine's own home-field bump is WORTH, in points, measured by running
# two identically-specified teams against each other under preseason conditions
# (player shock + preseason tilt -- this constant is only used on the preseason
# path). With the split 1.05 bump:
#
#     equal points both sides (40-41 totals) -> exp 20.7 vs 19.3, p_home ~0.538
#
# +1.35 points (the bump is a multiplier, so its point value scales with the
# level: +1.0 at a 30 total, +1.6 at 52; 1.35 is the working-range value). It matters when a
# margin is read off a MONEYLINE, because the market's price already contains a
# real home-field edge and the engine then adds its own on top. Subtracting the
# league's measured +0.78 there over-corrected by more than half a point and put
# the simulated home side 1.7pp under the market on 15 of 16 games -- one-signed,
# so a bias rather than noise. What has to come out is what the ENGINE adds.
_ENGINE_HFA_PTS = 1.35

# Share of an injected points split the engine's script lets survive to the
# realized mean margin (comebacks press, leaders kneel). Measured under
# preseason conditions at 0.775/0.787/0.808/0.819/0.795 for asked edges of
# 2/4/6/8/10 -- flat enough for one constant.
_EDGE_KEEP = 0.80


def profile_from_points(abbr, name, points, home, roster=None, props=None):
    """A synthetic team profile that scores `points` a game through the drive
    engine, with a full preseason player board underneath it.

    Everything scales off ONE measured team-game, so the players always sum to
    the team and the team always sums to the market's number."""
    import nfl_preseason as pre
    ask = max(6.0, points) / _ENGINE_BIAS
    scale = ask / pre.PRE_TEAM["points"]
    exp = pre.team_exp(scale)
    tds = exp["pass_td"] + exp["rush_td"]
    exp["fgm"] = tds * pre.TD_FG
    exp["rec_td"] = exp["pass_td"]
    players = []
    if roster:
        try:
            players = pre.stat_lines(roster, scale,
                                     force=[k[1] for k in (props or {})])
            _anchor(players, props, exp)
        except Exception:
            players = []
    return {"abbr": abbr, "name": name, "home": home, "players": players,
            "exp": exp}


def _anchor(players, props, exp):
    """Rewrite a player's expected stat to the level Kalshi's ladder implies, and
    give the rest of his position group what is left of the team's budget.

    Without the second half this would break the invariant the whole design rests
    on. The market says Carson Beck throws for ~105 in a game the model gives
    Arizona 177 passing yards total; taking that number and leaving the other two
    quarterbacks alone would hand the team 220 and quietly inflate every team-level
    leg the player legs are supposed to correlate WITH."""
    import nfl_preseason as pre
    if not props:
        return
    by_name = {pre._key(p["name"]): p for p in players}
    for (stat, nm), lad in props.items():
        p = by_name.get(nm)
        if not p or stat not in ("pass_yd", "rush_yd", "rec_yd", "rec"):
            continue
        want = pre.implied_mean(stat, lad.get("rungs"))
        if not want or want <= 0:
            continue
        pool = [q for q in players if q["pos"] == p["pos"]]
        tot = sum(q[stat] for q in pool)
        if tot <= 0:
            continue
        want = min(want, 0.85 * tot)            # nobody is the entire position group
        rest = tot - want
        others = sum(q[stat] for q in pool if q is not p)
        p[stat] = want
        if others > 0:
            for q in pool:
                if q is not p:
                    q[stat] *= rest / others


def simulate_preseason(home_ab, away_ab, home_name, away_name, implied, n=2400,
                       seed=None, ladders=None, rosters=None, props=None):
    """Market-anchored simulation of one exhibition. `implied` is
    kalshi_nfl.implied().

    Two grades of anchor, because Kalshi lists the two markets at different
    times. With a LADDER the market supplies both the level and the margin. With
    only a MONEYLINE it supplies the margin alone -- as a win probability, which
    the measured preseason margin distribution converts into points -- and the
    level falls back to the measured league-average exhibition, which is a real
    number rather than a guess and is right on average by construction."""
    import nfl_preseason as pre
    total = implied.get("total")
    total = max(20.0, float(total)) if total else 2.0 * pre.PRE_TEAM["points"]
    margin, fav = implied.get("margin"), implied.get("favourite")
    if margin is None:
        # No spread ladder: read the margin off the moneyline instead.
        p_win = implied.get("p_win") or {}
        p_home = p_win.get(kalshi_canon(home_ab))
        if p_home is None and p_win:
            p_home = 1.0 - (p_win.get(kalshi_canon(away_ab)) or 0.5)
        edge = pre.margin_from_prob(p_home) if p_home else 0.0
    else:
        margin = float(margin)
        edge = margin if fav == home_ab else (-margin if fav == away_ab else 0.0)
    # The market's number already contains the real home-field edge and the
    # engine will add its OWN on top, so the engine's comes back out first --
    # otherwise the home side is favoured twice. (Both anchor grades need this;
    # the ladder path used to skip it.) Then the asked edge is pre-amplified:
    # the script's comebacks and kneel-outs realize only ~80% of an injected
    # points split (measured 0.775-0.819 across the 2-10 point range), which
    # sent every market favourite back compressed toward a coin flip -- a 33c
    # home side came off the sim at 44c on a board whose whole claim on the
    # moneyline is to MATCH the market.
    if edge:
        edge = (edge - _ENGINE_HFA_PTS) / _EDGE_KEEP
    ph = max(6.0, (total + edge) / 2.0)
    pa = max(6.0, (total - edge) / 2.0)
    ros = rosters or {}
    # A player's ladder only belongs to HIS team's profile -- both sides of the
    # game share one Kalshi event, so the props map has to be split by roster.
    def mine(ab):
        names = {_nkey(p["name"]) for p in (ros.get(ab) or [])}
        return {k: v for k, v in (props or {}).items() if k[1] in names}
    return simulate_game(profile_from_points(home_ab, home_name, ph, True,
                                             ros.get(home_ab), mine(home_ab)),
                         profile_from_points(away_ab, away_name, pa, False,
                                             ros.get(away_ab), mine(away_ab)),
                         n=n, seed=seed, ladders=ladders, prop_lad=props,
                         shock=pre.PLAYER_LOGSD)


def _nkey(name):
    import nfl_preseason as pre
    return pre._key(name)


def kalshi_canon(ab):
    """ESPN's abbreviation in Kalshi's spelling. p_win is keyed the way Kalshi
    writes a team (WSH, JAX, LAR), and the schedule is keyed the way ESPN does,
    so a Washington or Jacksonville game would silently read no win probability
    at all and fall back to a pick'em."""
    try:
        import kalshi_nfl
        return kalshi_nfl._canon(ab)
    except Exception:
        return (ab or "").upper()


_BOARD_TTL = 1800


def board(week=1, preseason=False):
    """Non-blocking weekly slate: cached if fresh, else built in the background
    (Sleeper fetch + 16 drive-engine sims + Kalshi pricing).

    The finished board is published through boardshare so all gunicorn workers
    serve ONE build: kept per-worker, three workers ran three duplicate builds
    of the same slate (tripling the Kalshi fetch load) and the browser's polls
    flapped between "simulating..." and results depending on which worker
    answered."""
    import boardshare
    season = _season()
    key = ("nfl_slate", season, week, bool(preseason))
    name = f"nfl_slate_{season}_w{week}_{int(bool(preseason))}"
    hit = _cache.get(key)
    if hit and _time.time() - hit[0] < _BOARD_TTL:
        return hit[1]
    disk, age = boardshare.get(name, _BOARD_TTL)
    if disk is not None:                     # a sibling already built it
        _cache[key] = (_time.time() - age, disk)
        return disk
    if key not in _inflight and boardshare.claim(name):
        _inflight.add(key)

        def _bg():
            try:
                val = _build_board(season, week, preseason=preseason)
                if val is None:
                    # A week with no games (or a feed that came back empty) used
                    # to cache NOTHING, so board() returned None forever and the
                    # route answered "simulating in the background - retry
                    # shortly" for all eternity. Every poll also started ANOTHER
                    # build, so an empty week quietly turned into a thundering
                    # herd of rebuilds. Cache the emptiness, briefly, and say so.
                    val = {"season": season, "week": week, "preseason": bool(preseason),
                           "games": [], "n_games": 0, "empty": True,
                           "note": ("No games found for this week. Preseason runs "
                                    "weeks 1-4 in August; the regular season starts "
                                    "in September.")}
                    _cache[key] = (_time.time() - 1500, val)   # short TTL: retry in ~5m
                    boardshare.put(name, val, age=1500)
                else:
                    _cache[key] = (_time.time(), val)
                    boardshare.put(name, val)
            except Exception as e:
                # An exception used to vanish into the thread, leaving the board
                # None with nothing anywhere saying why.
                errlog.note("NFLG-board-build", e,
                            path=f"s{season} w{week} pre={int(bool(preseason))}")
                print(f"[nfl] board build failed (season={season} week={week} "
                      f"pre={preseason}): {e!r}", flush=True)
                val = {"season": season, "week": week, "preseason": bool(preseason),
                       "games": [], "n_games": 0, "empty": True, "error": str(e),
                       "note": "The slate could not be built; retrying shortly."}
                _cache[key] = (_time.time() - 1680, val)
                boardshare.put(name, val, age=1680)
            finally:
                _inflight.discard(key)
                boardshare.release(name)
        _threading.Thread(target=_bg, daemon=True).start()
    return hit[1] if hit else None


def _preseason_sims(season, week, n):
    """[(sim, suffix)] for the week's exhibitions, market-anchored.

    The regular-season board is seeded by Sleeper projections, which do not
    exist in August; these are anchored to the Kalshi ladder instead and carry a
    full measured player board underneath (nfl_preseason)."""
    import nfl_live, nfl_preseason, kalshi_nfl
    try:
        sched = nfl_live.schedule(week, season, seasontype=1) or []
    except Exception:
        return []
    try:
        idx = kalshi_nfl.index()
    except Exception:
        idx = {}
    try:
        ros = nfl_preseason.rosters(season) or {}
    except Exception:
        ros = {}
    out = []
    for gm in sched:
        h, a = gm.get("home"), gm.get("away")
        suffix = _suffix_for(idx, h, a)
        if not suffix:
            continue
        try:
            imp = kalshi_nfl.implied(suffix)
            lad = kalshi_nfl.ladders(suffix)
        except Exception:
            continue
        if not imp:
            continue
        sim = simulate_preseason(h, a, gm.get("home_name") or h,
                                 gm.get("away_name") or a, imp, n=n, ladders=lad,
                                 rosters={h: ros.get(h), a: ros.get(a)},
                                 props=(lad or {}).get("props"))
        sim["date"] = gm.get("date")
        sim["state"] = gm.get("state")
        sim["implied"] = imp
        out.append((sim, suffix))
    return out


def _build_board(season, week, n=2400, preseason=False):
    import nfl_data
    if preseason:
        sims = [s for s, _suf in _preseason_sims(season, week, n)]
        if not sims:
            return None
        games = None
    else:
        games = nfl_data.week_games(str(season), week)
        if not games:
            return None
        sims = None
    try:
        import kalshi_nfl
        kx = True
    except Exception:
        kx = False
    try:
        import calibrate
        cal = lambda p: max(0.03, min(0.97, calibrate.apply("nfl", p)))
    except Exception:
        cal = lambda p: p

    out, log_rows = [], []
    # A preseason model has no independent read on the level -- it is anchored TO
    # the market -- so the calibrator, which corrects a model against its own
    # graded record, has nothing to correct and is left out of that path.
    for sim in (sims if preseason else
                (simulate_game(h, a, n=n) for h, a in games)):
        raw_ph = sim["p_home"]
        if preseason:
            cal_used = lambda p: p
        else:
            cal_used = cal
        ph = cal_used(raw_ph)
        g = {k: sim[k] for k in ("home", "away", "home_name", "away_name", "date",
                                 "state", "exp_home", "exp_away", "exp_total",
                                 "mean_margin", "spread_ladder", "n_sims")}
        g["p_home"] = round(ph, 4)
        g["p_away"] = round(1 - ph, 4)
        g["p_home_raw"] = raw_ph
        g["total_ladder"] = [r for r in sim["total_ladder"]
                             if 20 <= r["over_pct"] <= 80][:5] or sim["total_ladder"][:3]
        g["players"] = sim["players"][:10]
        g["props"] = sorted(sim["props"], key=lambda p: -p["over_pct"])[:10]

        # Kalshi moneyline + edge (net of the calibrated prob).
        if kx:
            try:
                px = kalshi_nfl.game_prices(sim["home"], sim["away"])
            except Exception:
                px = None
            if px:
                g["kalshi"] = {"home_cents": px["home_cents"], "away_cents": px["away_cents"]}
                if px["home_cents"] is not None:
                    g["edge_home"] = round(ph * 100 - px["home_cents"], 1)
                if px["away_cents"] is not None:
                    g["edge_away"] = round((1 - ph) * 100 - px["away_cents"], 1)
                # Log the RAW model prob per side for the calibrator (predlog
                # dedups by ticker, so re-logging a week is harmless), beside the
                # de-vigged price so vs_market can answer for football too.
                _hc, _ac = px.get("home_cents"), px.get("away_cents")
                for side, tk, p, own, opp in (
                        ("home", px.get("home_ticker"), raw_ph, _hc, _ac),
                        ("away", px.get("away_ticker"), 1 - raw_ph, _ac, _hc)):
                    if tk:
                        log_rows.append((tk, p, px.get("close"),
                                         predlog.devig(own, opp)))

        # Model pick (calibrated) + default same-game parlay.
        pick_home = ph >= 0.5
        g["pick"] = {"team": sim["home" if pick_home else "away"],
                     "name": sim["home_name" if pick_home else "away_name"],
                     "pct": round((ph if pick_home else 1 - ph) * 100, 1)}
        try:
            g["sgp"] = same_game_parlay(sim, n_legs=3, target=0.45)
        except Exception:
            g["sgp"] = None
        out.append(g)

    if log_rows:
        try:
            predlog.init_db()          # safe no-op when already initialized
            # Preseason keeps its OWN bucket. The apply side already refuses to
            # calibrate exhibitions, but the record side did not, so every
            # August game was being graded into the regular-season model's
            # evidence -- and the two are not the same distribution. Measured
            # over 2023-25 (n=147/801): total 38.6 against 45.2, home edge
            # -0.2 against +2.1, and the probability SOURCE differs (Kalshi's
            # own ladder against Sleeper projections). Fitting one temperature
            # across both would learn a blend of two shapes and apply it to
            # games that only ever have one of them. ~65 exhibition games a
            # year against ~285 regular ones, so the contamination would have
            # been steady rather than dramatic, which is worse: it never gets
            # big enough to look obviously wrong.
            predlog.log_many("nfl_pre" if preseason else "nfl", log_rows)
        except Exception as _e:
            errlog.note("NFLG-build_board", _e)
    out.sort(key=lambda g: g["date"] or "")
    note = ("Drive-level Monte Carlo seeded by Sleeper's matchup-adjusted "
            "projections: alternating possessions with game script, short "
            "fields and OT; player lines are dealt from the simulated team "
            "game, so props and same-game parlays carry real correlation.")
    if preseason:
        note = ("Preseason: the LEVEL comes from Kalshi's de-vigged ladder, because "
                "no projection source covers exhibitions. The engine supplies the "
                "joint structure a parlay needs and a set of separately-priced "
                "lines does not. Player usage is measured off 96 team-games of "
                "last preseason and runs INVERTED -- backups and rookies take the "
                "snaps -- and where Kalshi books a player his ladder sets his level. "
                "No edge is claimed on the total; the edge claimed is on correlation.")
    return {"season": season, "week": week, "engine": "drive", "n_games": len(out),
            "n_sims": n, "games": out, "preseason": bool(preseason), "note": note}


# --- COMBO MAKER ---------------------------------------------------------------
# Baseball's builder, on NFL legs. Everything downstream is already shared: the
# candidates carry the same mask/marg/group/kref shape mlb_sim emits, so
# game_bundles gives correlation-aware same-game stacks and combo_engine does the
# cross-game DP and the price-aware choice. Only the pricing source differs.
def price_cands(cands, suffix, blend=True):
    """Annotate each candidate with its live Kalshi ask and market-blended
    probability, in place -- before bundles are built, so the bundle's joint is
    computed on the number the user is actually shown."""
    import combo_engine
    try:
        import kalshi_nfl
        idx = kalshi_nfl.index()
    except Exception:
        idx = {}
    quotes = {}
    for c in cands:
        px, q = None, None
        if idx and suffix:
            try:
                px = kalshi_nfl.price_leg(idx, suffix, c.get("kref"))
                q = kalshi_nfl.quote_leg(idx, suffix, c.get("kref"))
            except Exception:
                px, q = None, None
        c["price_cents"] = px
        # The full quote, not None. Passing None here marked every NFL leg
        # untradeable, so combo_engine charged the whole slate at fair value and
        # reported priced_frac 0.0 against a board of live asks.
        quotes[id(c)] = q
    if blend:
        combo_engine.blend_candidates(cands, quotes)
    return cands


def build_parlay(week=1, preseason=False, n_legs=4, target_pct=55, cap_pct=None,
                 target_payout=0, max_legs_per_game=3, max_total_legs=8,
                 legs_mode="prefer", payout_mode="off", conn="or",
                 objective="balanced", n_sims=3000, types=None, game_sel=None,
                 max_bet=False, cap_x=None):
    """One parlay across the week's NFL games, priced against Kalshi.

    `cap_pct` turns the confidence floor into a band exactly as it does in
    baseball: the spread and total ladders now carry every line Kalshi books, so
    the builder walks to the one that lands inside it.

    `max_bet` swaps every target for Kalshi's payout ceiling -- the likeliest
    slip that still collects the full capped payout. See combo_engine.max_bet."""
    import combo_engine
    import mlb_sim

    floor = max(0.05, min(0.97, target_pct / 100.0))
    ceil = 1.0
    if cap_pct is not None and cap_pct / 100.0 > floor:
        ceil = min(1.0, cap_pct / 100.0)

    games = _slate_sims(week, preseason, n_sims)
    games_bundles = []
    for g in games:
        if game_sel and g["suffix"] not in game_sel:
            continue
        cands = [c for c in g["cands"]
                 if (types is None or c["type"] in types)]
        if not cands:
            continue
        price_cands(cands, g["suffix"])
        cands = [c for c in cands if floor <= c["marg"] <= ceil]
        # Same optimism bound as baseball: a max bet multiplies prices, so a leg
        # the model likes far more than the market can carry the slip alone.
        if max_bet:
            cands = [c for c in cands
                     if combo_engine.stackable(c["marg"], c.get("price_cents"))]
        if not cands:
            continue
        # Floor of 1, not 2 -- same bug as baseball carried. With same-game off
        # the caller passes max_legs_per_game=1 and a floor of 2 stacked anyway.
        depth = max(1, min(max_legs_per_game, max(n_legs, 3), max_total_legs))
        bundles = mlb_sim.game_bundles(cands, g["n"], max_legs=depth)
        if bundles:
            games_bundles.append((g["label"], bundles, g["suffix"]))
    if not games_bundles:
        return None

    # One leg per game on a one-game board cannot reach two legs, and that is a
    # sentence the caller should be able to say rather than shrug at.
    if len(games_bundles) < 2 and max_legs_per_game <= 1:
        return {"error_hint": "single_game_no_stack", "n_games_available": len(games_bundles)}
    states = combo_engine.frontier(games_bundles, max_total_legs=max_total_legs,
                                   net=True)
    if max_bet:
        # Same reasoning as baseball: the ceiling is the target, so the leg and
        # payout preferences have nothing left to bind.
        targets = {}
        best, meta = combo_engine.max_bet(states, cap=cap_x)
    else:
        targets = {"legs_target": n_legs, "payout_target": target_payout,
                   "legs_mode": legs_mode, "payout_mode": payout_mode, "conn": conn}
        best, meta = combo_engine.choose(states, objective=objective, **targets)
    if not best:
        return None
    item = mlb_sim._mixed_item(best["sel"], games_bundles,
                               None if max_bet else
                               (target_payout if payout_mode != "off" else None))
    for k, v in meta.items():
        if k != "objective" and v is not None:
            item[k] = v
    item["objective"] = "max_bet" if max_bet else objective
    item["legs_target"] = None if max_bet else (n_legs if legs_mode != "off" else None)
    if max_bet:
        # Same trap as baseball: _mixed_item's payout_reached defaults to True
        # with no fair-payout target, which would claim every max bet succeeded.
        item["payout_reached"] = meta.get("cap_reached")
        item["target_payout_x"] = None
    item["leg_floor_pct"] = round(floor * 100, 1)
    item["leg_cap_pct"] = round(ceil * 100, 1) if ceil < 1.0 else None
    item["preseason"] = bool(preseason)
    item["cost_x"] = round(best["cost"], 4)
    item["market_payout_x"] = round(best["payout"], 2) if best["payout"] else None
    item["ev_pct"] = round(best["ev"] * 100, 1) if best["ev"] is not None else None
    item["kelly_pct"] = round(combo_engine.kelly(best["prob"], best["cost"]) * 100, 2)
    item["priced_frac"] = round(best["priced_frac"], 2)
    item["priced_legs"] = best["priced"]
    if not max_bet:
        item["alternatives"] = combo_engine.compare(states, best, **targets)
    item["n_sims"] = n_sims
    return item


def _slate_sims(week, preseason, n_sims):
    """[{label, suffix, cands, n}] for the week -- market-anchored in preseason,
    profile-driven in the regular season."""
    import nfl_live
    season = _season()
    out = []
    try:
        sched = nfl_live.schedule(week, season,
                                  seasontype=1 if preseason else 2) or []
    except Exception:
        sched = []
    idx = {}
    try:
        idx = kalshi_index()
    except Exception:
        idx = {}
    for gm in sched:
        h, a = gm.get("home"), gm.get("away")
        suffix = _suffix_for(idx, h, a)
        if not suffix:
            continue
        try:
            import kalshi_nfl
            imp = kalshi_nfl.implied(suffix)
            lad = kalshi_nfl.ladders(suffix)
        except Exception:
            imp = lad = None
        if preseason:
            if not imp:
                continue                       # no market -> nothing to anchor to
            import nfl_preseason
            try:
                ros = nfl_preseason.rosters(season) or {}
            except Exception:
                ros = {}
            sim = simulate_preseason(h, a, gm.get("home_name") or h,
                                     gm.get("away_name") or a, imp,
                                     n=n_sims, ladders=lad,
                                     rosters={h: ros.get(h), a: ros.get(a)},
                                     props=(lad or {}).get("props"))
        else:
            import nfl_data
            pair = next(((th, ta) for th, ta in
                         (nfl_data.week_games(str(season), week) or [])
                         if th.get("abbr") == h and ta.get("abbr") == a), None)
            if not pair:
                continue
            sim = simulate_game(pair[0], pair[1], n=n_sims, ladders=lad)
        out.append({"label": f"{gm.get('away_name') or a} @ {gm.get('home_name') or h}",
                    "suffix": suffix, "cands": sim["_masks"], "n": sim["n_sims"]})
    return out


def kalshi_index():
    import kalshi_nfl
    return kalshi_nfl.index()


def _suffix_for(idx, home, away):
    """Kalshi event suffix for a matchup, matched on the team pair."""
    import kalshi_nfl
    want = frozenset({kalshi_nfl._canon(home), kalshi_nfl._canon(away)})
    for suffix, e in (idx or {}).items():
        if e.get("pair") == want:
            return suffix
    return None
