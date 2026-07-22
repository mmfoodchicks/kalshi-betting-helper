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
import random

# ---- Engine constants -------------------------------------------------------
_DRIVES = 10.7            # nominal possessions per team per game
_HFA_SCORE = 1.03         # home bump on per-drive scoring (~+0.6 pts/game)
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


def _rates(prof, home):
    """Per-drive outcome rates from a team's expected production."""
    e = prof["exp"]
    tds = e["pass_td"] + e["rush_td"]
    hfa = _HFA_SCORE if home else 1.0
    p_td = max(0.04, min(0.55, tds / _DRIVES * hfa * _CAL))
    p_fg = max(0.02, min(0.40, e["fgm"] / _DRIVES * hfa * _CAL))
    p_to = max(0.02, min(0.30, (e["pass_int"] + e["fum_lost"]) / _DRIVES))
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


def _play_game(rh, ra, rng):
    """One simulated game. Returns (pts_h, pts_a, stats) where stats carries each
    side's realized TD/FG counts, possession count and pass/run tilt."""
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


def simulate_game(home, away, n=2400, seed=None):
    """Correlated Monte Carlo of one game from two nfl_data team profiles.

    Returns win probs, score/margin/total distributions, per-player prop
    probabilities (yardage ladders, receptions, anytime TD) and the bitmask
    candidate legs the MLB parlay machinery consumes."""
    rng = random.Random(seed if seed is not None
                        else hash((home["abbr"], away["abbr"])) & 0xFFFFFFFF)
    rh, ra = _rates(home, home.get("home") is not False), _rates(away, False)
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
        g = _play_game(rh, ra, rng)
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
                noise = max(0.1, rng.gauss(1.0, _PLAYER_SD))
                if p["pass_yd"] > 0:
                    L["pass_yd"][s] = p["pass_yd"] / (tot["pass_yd"] or 1) * team_pass \
                        * max(0.5, rng.gauss(1.0, 0.08))
                    L["pass_td"][s] = pass_td_n if p["pass_yd"] / tot["pass_yd"] > 0.7 \
                        else _pois(p["pass_td"] * pass_mult, rng)
                if p["rush_yd"] > 0:
                    L["rush_yd"][s] = p["rush_yd"] / tot["rush_yd"] * team_rush * noise
                if p["rec_yd"] > 0:
                    rn = max(0.1, rng.gauss(1.0, _PLAYER_SD))
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

    # Player boards + prop ladders.
    players, props = [], []
    for i, p in enumerate(all_ps):
        L = lines[i]
        row = {"name": p["name"], "pos": p["pos"], "team": team_of[i]}
        for key, lab in (("pass_yd", "pass yds"), ("rush_yd", "rush yds"),
                         ("rec_yd", "rec yds"), ("rec", "receptions")):
            arr = L[key]
            mean = sum(arr) / n
            if mean < (2.5 if key == "rec" else 25):
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
        if td1 >= 0.12:
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
                               p_home, n),
    }


# ---- Bitmask candidate legs (feeds mlb_sim's SGP machinery) -----------------
def _mask_of(pred, n):
    m = 0
    for i in range(n):
        if pred(i):
            m |= (1 << i)
    return m


def _build_masks(home, away, hp, ap, lines, margins, totals, p_home, n):
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
    for m in (3, 7, 10):
        add("Spread", f"{h} wins by {m}+", _mask_of(lambda i, m=m: margins[i] >= m, n), "Spread")
        add("Spread", f"{a} wins by {m}+", _mask_of(lambda i, m=m: margins[i] <= -m, n), "Spread")
    mean_total = sum(totals) / n
    for d in (-4, 0, 4):
        line = round(mean_total) + d + 0.5
        add("Total", f"Over {line}", _mask_of(lambda i, L=line: totals[i] > L, n),
            "Total", avg=round(mean_total, 1), unit="points")
        add("Total", f"Under {line}", _mask_of(lambda i, L=line: totals[i] < L, n),
            "Total", avg=round(mean_total, 1), unit="points")

    all_ps = list(hp) + list(ap)
    for i, p in enumerate(all_ps):
        L = lines[i]
        nm = p["name"]
        for key, lab, step in (("pass_yd", "pass yds", 25), ("rush_yd", "rush yds", 15),
                               ("rec_yd", "rec yds", 15), ("rec", "receptions", 1)):
            arr = L[key]
            mean = sum(arr) / n
            if mean < (3 if key == "rec" else 30):
                continue
            line = (math.floor(mean / step) * step) + 0.5
            add(lab.title(), f"{nm} {line}+ {lab}",
                _mask_of(lambda i2, A=arr, ln=line: A[i2] > ln, n),
                f"{nm}:{key}", avg=round(mean, 1), unit=lab)
        if sum(1 for x in L["td"] if x >= 1) / n >= 0.15:
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


def board(week=1):
    """Non-blocking weekly slate: cached if fresh, else built in the background
    (Sleeper fetch + 16 drive-engine sims + Kalshi pricing)."""
    season = _season()
    key = ("nfl_slate", season, week)
    hit = _cache.get(key)
    if hit and _time.time() - hit[0] < 1800:
        return hit[1]
    if key not in _inflight:
        _inflight.add(key)

        def _bg():
            try:
                val = _build_board(season, week)
                if val is not None:
                    _cache[key] = (_time.time(), val)
            finally:
                _inflight.discard(key)
        _threading.Thread(target=_bg, daemon=True).start()
    return hit[1] if hit else None


def _build_board(season, week, n=2400):
    import nfl_data
    games = nfl_data.week_games(str(season), week)
    if not games:
        return None
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
    for h, a in games:
        sim = simulate_game(h, a, n=n)
        raw_ph = sim["p_home"]
        ph = cal(raw_ph)
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
                import kalshi_nfl
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
                # dedups by ticker, so re-logging a week is harmless).
                for side, tk, p in (("home", px.get("home_ticker"), raw_ph),
                                    ("away", px.get("away_ticker"), 1 - raw_ph)):
                    if tk:
                        log_rows.append((tk, p, px.get("close")))

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
            import predlog
            predlog.init_db()          # safe no-op when already initialized
            predlog.log_many("nfl", log_rows)
        except Exception:
            pass
    out.sort(key=lambda g: g["date"] or "")
    return {"season": season, "week": week, "engine": "drive", "n_games": len(out),
            "n_sims": n, "games": out,
            "note": "Drive-level Monte Carlo seeded by Sleeper's matchup-adjusted "
                    "projections: alternating possessions with game script, short "
                    "fields and OT; player lines are dealt from the simulated team "
                    "game, so props and same-game parlays carry real correlation."}
