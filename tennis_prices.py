"""Live Kalshi tennis match board: our model vs the market, with the edges and the
'what to look for' read-outs.

Kalshi lists each singles match as two winner markets (KXATPMATCH / KXWTAMATCH),
one per player, sharing an event prefix. We pull the open slate, pair the players,
run the hierarchical sim (tennis_sim) on their charted serve/return profiles, and:
  - blend our model win% toward the de-vig market by confidence (thin profiles
    defer to the market, like the UFC engine), surfacing the edge;
  - expose the coherent derived markets (total games, total sets, straight sets,
    set winners, aces) for the combo maker;
  - translate the numbers into plain-English angles -- serve dominance, surface
    edge, likely straight-setter, over/under games lean, model-vs-market value --
    so a non-expert can see what to look for.
"""

import datetime
import clock
import threading
import time
import unicodedata

import kalshi
import racing
import tennis_data as td
import tennis_elo
import tennis_sim as ts

_ELO_MIN = 8              # min settled matches before an Elo-only read is trusted
# How hard the model defers to a liquid market, by source. Charting is reliable
# (small k -> keep our read); a provisional Elo on an obscure ITF player is not,
# so it defers hard (large k) -- a 5-match Elo never overrides a liquid price.
_BLEND_K = {"serve": 12.0, "serve+elo": 14.0, "elo": 30.0, "market": 12.0}
# Serve-sim share of the win probability: _SERVE_CAP * n/(n + _SERVE_K), where n is
# the charted-match depth behind the THINNER player. Fitted -- see the long note at
# the use site. ~0.00 on an unknown junior, ~0.10 at 20 charted matches, ~0.17 at
# 50, approaching 0.30 for the best-documented pairs.
_SERVE_CAP, _SERVE_K = 0.30, 40.0

# (display label, Kalshi series, data tour-code). ATP/WTA are the charted tours;
# ITF is the lowest pro tier -- its players are mostly absent from the Match
# Charting Project, so those matches show market-priced (de-vig) with no model
# edge rather than a fabricated one. ITF men use the men's serve model, ITF women
# the women's.
_TOURS = [
    ("ATP", "KXATPMATCH", "m"),
    ("WTA", "KXWTAMATCH", "w"),
    ("ITF", "KXITFMATCH", "m"),
    ("ITF-W", "KXITFWMATCH", "w"),
]
_form = racing._form_cache
_inflight = {}


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join("".join(c for c in s.lower() if c.isalnum() or c == " ").split())


_FATIGUE_W = {0: 1.0, 1: 0.8, 2: 0.6, 3: 0.42, 4: 0.28, 5: 0.18, 6: 0.1}


def _comp_day(comp, today):
    """Days ago this match was actually PLAYED, from the match's own timestamp.

    Essential, not cosmetic: ESPN's tennis scoreboard ignores the `dates` filter
    for completed matches inside a live event -- querying any day of a tournament
    returns every match already played there. Measured on a real slate, 133 of 172
    completed matches came back on all five days crawled. Keying recency off the
    QUERY day therefore counted one match up to seven times, each at a different
    weight, and took rest_days = min(query offsets) = 0 for anyone whose event was
    still running. Loads ran ~3.4x high and rest_days only ever came back 0, 3 or
    4 -- never 1, 2, 5 or 6, which is the tell."""
    ds = (comp.get("date") or comp.get("startDate") or "")[:10]
    if len(ds) != 10:
        return None
    try:
        d = datetime.date(int(ds[:4]), int(ds[5:7]), int(ds[8:10]))
    except ValueError:
        return None
    return (today - d).days


def _fatigue_index():
    """{norm_name: {load, rest_days, recent, wins, losses, streak}} from a short
    crawl of ESPN's dated tennis scoreboards.

    `load` is recency-weighted recent sets played (a 3-setter yesterday costs more
    than a 2-setter last week); `rest_days` is days since the player's last match.
    Together these are the differential fatigue signal the sim uses -- tennis is
    brutal on tired legs, especially in deciding sets.

    `wins`/`losses`/`streak` are recent RESULTS. The same payload carries the
    winner flag, so form comes free with the fatigue crawl. It is reported for
    context only and deliberately does NOT move the probability: measured over
    19,278 settled matches, a form or streak term added to the rating produced no
    out-of-sample gain (tests/tennis_form_check.py). Displayed, not modelled."""
    def build():
        today = clock.today_et()
        agg = {}
        seen = set()                       # ESPN repeats a match on every event day
        for tour in ("atp", "wta"):
            for i in range(7):
                d = today - datetime.timedelta(days=i)
                ds = d.strftime("%Y%m%d")
                try:
                    j = racing._get_json(
                        f"https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard?dates={ds}",
                        timeout=12)
                except Exception:
                    continue
                for e in j.get("events", []):
                    for g in (e.get("groupings") or []):
                        for c in (g.get("competitions") or []):
                            if c.get("status", {}).get("type", {}).get("state") != "post":
                                continue
                            cid = c.get("id") or c.get("uid")
                            if cid in seen:
                                continue          # already counted on another day
                            seen.add(cid)
                            ago = _comp_day(c, today)
                            if ago is None or not (0 <= ago <= 6):
                                continue
                            comps = c.get("competitors") or []
                            sets = max((len(x.get("linescores") or []) for x in comps), default=2) or 2
                            for x in comps:
                                nm = _norm((x.get("athlete") or {}).get("displayName"))
                                if not nm:
                                    continue
                                a = agg.setdefault(nm, {"load": 0.0, "rest_days": 99,
                                                        "recent": [], "wins": 0, "losses": 0})
                                a["load"] += _FATIGUE_W.get(ago, 0.0) * sets
                                a["rest_days"] = min(a["rest_days"], ago)
                                won = bool(x.get("winner"))
                                a["wins" if won else "losses"] += 1
                                a["recent"].append({"days_ago": ago, "sets": sets,
                                                    "won": won})
        for a in agg.values():
            # newest first, and a signed streak off the ordered results
            a["recent"].sort(key=lambda r: r["days_ago"])
            a["recent"] = a["recent"][:6]
            s = 0
            for r in a["recent"]:
                if s and (r["won"] != a["recent"][0]["won"]):
                    break
                s += 1
            a["streak"] = s if a["recent"] and a["recent"][0]["won"] else -s
        return agg
    return racing._cached(("tennis_fatigue",), 3 * 3600, build) or {}


def _surface_of(tournament):
    """Surface from a tournament NAME ('M25 Koszalin'), or None if unrecognised.

    Thin wrapper so the ITF board can use the same keyword map the ATP/WTA board
    does -- tennis_live is imported lazily elsewhere in this module, so keep that
    shape here too."""
    if not tournament:
        return None
    try:
        import tennis_live
        return tennis_live.surface_of(tournament)
    except Exception:
        return None


def _tourney_from_title(title):
    """'Will X win the A vs B: M15 Villa Constitucion Round of 32 match?' ->
    'M15 Villa Constitucion' (the Kalshi event's tournament — how the user finds
    it among the dozens of tennis tabs). Strips the trailing round descriptor."""
    import re
    m = re.search(r":\s*(.+?)\s+match\??\s*$", title or "", re.I)
    if not m:
        return None
    t = m.group(1).strip()
    # Drop a trailing round descriptor ("Round Of 32", "Quarterfinal", "R16",
    # "Final") whether it trails a tournament or IS the whole string (ATP/WTA
    # main-tour titles carry only the round, no tournament name).
    t = re.sub(r"(^|\s+)(round\s+of\s+\d+|r\d+|quarterfinals?|semifinals?|"
               r"finals?|round\s+robin)\s*$", "", t, flags=re.I).strip()
    return t or None


def _match_markets(series):
    """Open winner markets grouped by event ->
    {event: {'players': [...], 'tournament': str|None}}."""
    out, cursor = {}, ""
    for _ in range(8):
        url = f"{kalshi.BASE}/markets?series_ticker={series}&status=open&limit=1000"
        if cursor:
            url += f"&cursor={cursor}"
        try:
            d = kalshi._get_json(url)
        except Exception:
            break
        for m in d.get("markets", []):
            tk = m.get("ticker") or ""
            ev = tk.rsplit("-", 1)[0] if "-" in tk else tk     # drop the -PLAYER suffix
            nm = m.get("yes_sub_title")
            if not nm:
                continue
            rec = out.setdefault(ev, {"players": [], "tournament": None})
            rec["players"].append({
                "name": nm, "ticker": tk,
                "cents": kalshi._cents(m.get("yes_ask_dollars"))})
            if not rec["tournament"]:
                rec["tournament"] = _tourney_from_title(m.get("title"))
        cursor = d.get("cursor") or ""
        if not cursor:
            break
    return out


def _event_date(ev):
    """KXATPMATCH-26JUN26ABCDEF... -> '20260626' (best effort)."""
    import re
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", ev or "")
    if not m:
        return clock.today_et().strftime("%Y%m%d")
    yy, mon, dd = m.groups()
    months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
              "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
    return f"20{yy}{months.get(mon, 1):02d}{dd}"


def _insights(a, b, sim, surface, trusted=True):
    """Plain-English angles. Favorite + the 'strong favorite / coin-flip' framing
    key off `fair_win` — the confidence-blended number we actually believe — NOT
    the raw serve sim, which on thin data can wildly favor the wrong player. The
    serve-model narrative (serves bigger / straight sets) is only emitted when the
    charting is trustworthy (`trusted`); on thin data it's noise and is suppressed
    so the story never contradicts the fair number."""
    out = []
    fw = lambda p: p.get("fair_win") if p.get("fair_win") is not None else p.get("model_win") or 0
    fav, dog = (a, b) if fw(a) >= fw(b) else (b, a)
    # Serve-model narrative only when we trust the charting (else it's a
    # small-sample artifact that can contradict the fair read).
    if trusted:
        # serve dominance
        if abs(sim["holdA"] - sim["holdB"]) >= 6:
            s = a if sim["holdA"] > sim["holdB"] else b
            out.append(f"🎾 {s['name'].split()[-1]} serves bigger (holds {max(sim['holdA'], sim['holdB'])}% vs {min(sim['holdA'], sim['holdB'])}%)")
        # Surface edge: player's surface serve rate well above their overall. Only
        # when we actually know the surface -- an unidentified stop is modelled on
        # the surface-agnostic overall profile, where this comparison is vacuous.
        for p in (a, b) if surface else ():
            prof = p.get("prof")
            if not prof:
                continue
            ov = prof["overall"]["spw"]
            sv = (prof.get("surf") or {}).get(surface, {}).get("spw", ov)
            if sv - ov >= 0.02 and (prof.get("surf") or {}).get(surface, {}).get("w", 0) >= 5:
                out.append(f"📈 {p['name'].split()[-1]} is stronger on {surface.lower()}")
        # straight-set likelihood (only meaningful when A is the fair favorite)
        if fw(a) >= fw(b) and sim["a_straight"] >= 52:
            out.append(f"🧹 {a['name'].split()[-1]} often in straight sets ({sim['a_straight']}%)")
    # Favorite framing — always from fair_win, so it matches the headline + lean.
    if 42 <= fw(fav) <= 58:
        out.append(f"⚖️ Coin-flip — lean Over {sim['mean_games']:.0f} games / 3 sets")
    elif fw(fav) >= 72:
        out.append(f"💪 {fav['name'].split()[-1]} the favorite ({round(fw(fav))}%)")
    if not trusted:
        out.append("🔴 Thin charting on these players — deferring to the market, so the win% is the market's read, not a strong model edge.")
    # model vs market value
    for p in (a, b):
        if p.get("edge") is not None and p["edge"] >= 6:
            out.append(f"💰 Value: model has {p['name'].split()[-1]} at {p['fair_win']}% vs {p['cents']}¢")
    # ace-heavy
    if sim["aces_total"] >= 18:
        out.append(f"🔥 Big-serving match (~{sim['aces_total']:.0f} aces) — ace overs in play")
    # fatigue differential
    fa, fb = a.get("fatigue"), b.get("fatigue")
    la, lb = (fa or {}).get("load", 0.0), (fb or {}).get("load", 0.0)
    if abs(la - lb) >= 2.2:
        fresh, tired = (a, b) if la < lb else (b, a)
        tf = tired.get("fatigue") or {}
        rec = (tf.get("recent") or [{}])[0]
        when = "today" if rec.get("days_ago") == 0 else f"{rec.get('days_ago')}d ago" if rec.get("days_ago") else "recently"
        sets = rec.get("sets")
        detail = f"{sets}-set match {when}" if sets else "a heavier recent load"
        out.append(f"😮‍💨 Fatigue edge: {fresh['name'].split()[-1]} is fresher ({tired['name'].split()[-1]} had {detail})")
    # Surface specialists. The rating now splits by court, so say when a player is
    # materially better or worse on this one than they are overall -- this is the
    # "great player, but not on clay" effect, and it is the single biggest thing
    # the model was blind to before.
    if surface:
        for p in (a, b):
            # Compare this surface against the player's OTHER surfaces, not
            # against their overall rating. Surface ratings update only on their
            # own surface, so they lag the overall as a matter of arithmetic --
            # comparing to it called Shelton "weaker on hard" when hard is his
            # best court by nearly 100 Elo. Surface-to-surface is the claim a
            # reader actually wants.
            sp = p.get("elo_by_surface") or {}
            here = sp.get(surface)
            others = [(v["elo"], v["n"]) for k, v in sp.items()
                      if k != surface and v.get("n", 0) >= 10]
            if not here or here.get("n", 0) < 15 or not others:
                continue
            tot = sum(n for _, n in others)
            elsewhere = sum(e * n for e, n in others) / tot
            gap = here["elo"] - elsewhere
            if abs(gap) < 40:               # below this it is not a specialism
                continue
            last = p["name"].split()[-1]
            word = "stronger" if gap > 0 else "weaker"
            out.append(f"🎾 {last} is a {surface.lower()}-court {'specialist' if gap > 0 else 'weak spot'}: "
                       f"{gap:+.0f} Elo vs their other surfaces "
                       f"({here['n']} matches on {surface.lower()})")
    # Recent form. Flagged only at the extremes, and always as CONTEXT -- the
    # backtest in tennis_elo.form found no out-of-sample edge in a form term, so
    # this never claims the price is wrong, it just says what has been happening.
    # Thresholds are set against what is actually OBSERVABLE, which is lopsided:
    # in a knockout draw everyone still playing won their last match, so on a real
    # board ~73% of players sit at streak +1 and barely 2% are negative. A player
    # on a losing run is, by construction, already out of the tournament -- they
    # only reappear at the start of the next one. "Fade the slump" therefore has
    # very little surface area in tennis, which is why a losing streak of 2 is
    # already noteworthy while a winning streak needs 5 to mean anything.
    for p in (a, b):
        f = p.get("form")
        if not f or f["n"] < 4:
            continue
        last = p["name"].split()[-1]
        if f["streak"] <= -2:
            out.append(f"❄️ {last} arrives on a {-f['streak']}-match losing run "
                       f"({f['w']}-{f['l']} over the last {f['n']}) — context, not priced in")
        elif f["streak"] >= 5:
            out.append(f"🔥 {last} has won {f['streak']} straight "
                       f"({f['w']}-{f['l']} over the last {f['n']}) — context, not priced in")
        elif f["delta"] <= -0.15:
            out.append(f"📉 {last} is {f['w']}-{f['l']} recently, below what that "
                       f"rating implies — context, not priced in")
    return out[:7]


def _build_match(tour_label, ev, players, n_sims, fatigue_idx=None, tcode="m",
                 slam_names=None, tournament=None, surface_map=None, tourn_map=None,
                 serp_surfaces=None,
                 start_map=None):
    if len(players) != 2:
        return None
    date = _event_date(ev)
    # Scheduled start time (from the ESPN schedule, by player name) for the tile.
    stmap = start_map or {}
    start = (stmap.get(_norm(players[0]["name"])) or stmap.get(_norm(players[1]["name"])))
    # Main-tour (ATP/WTA) Kalshi titles carry only the round, so `tournament`
    # is often None there. Fill the host city from the ESPN schedule — that's
    # exactly how Kalshi labels the tab (ATP Bastad, WTA Athens).
    if not tournament and tourn_map:
        tournament = (tourn_map.get(_norm(players[0]["name"]))
                      or tourn_map.get(_norm(players[1]["name"])))
    # Real surface, in order of reliability: the ESPN tournament map, then the
    # tournament NAME off the Kalshi title. That second route is the only one that
    # ever reaches ITF -- surface_map is built from ESPN scoreboards, which carry
    # ATP/WTA only (45 players on a 219-match board) -- and it was previously never
    # consulted, so the ITF clay towns in tennis_live._CLAY_KW were dead code.
    smap = surface_map or {}
    surface = (smap.get(_norm(players[0]["name"])) or smap.get(_norm(players[1]["name"]))
               or _surface_of(tournament)
               # Last resort before giving up: a cached Google lookup for this
               # stop (serp_surface). Only reached when the keyword map has no
               # opinion, which for ITF is most of the board.
               or (serp_surfaces or {}).get(tournament))
    # Nothing matched. What that MEANS depends on the tier, and conflating the two
    # is what made this wrong in both directions at different times.
    #
    # On the ATP/WTA tour, falling through really does mean hard court: the tour
    # calendar is small and enumerable, and tennis_live's keyword lists name its
    # clay and grass stops specifically, so the residual is the hard swing. That
    # was the original design and it was right -- for the tour.
    #
    # At ITF it is not: hundreds of stops a year, no list can cover them, and in
    # the European summer most of the ones that fall through are clay. Defaulting
    # those to Hard modelled clay matches on hard-court serve numbers. They stay
    # UNKNOWN and get the player's surface-agnostic overall profile instead
    # (match_rates falls back to `overall` for any key it doesn't hold), which is
    # the honest answer when we genuinely do not know.
    if surface is None and not tour_label.startswith("ITF"):
        surface = "Hard"
    surf_key = surface or "__unknown__"
    # Best-of-5 only for men's Grand Slam matches. The ticker carries NO
    # tournament (just [date][3 letters per player] — "NARdi GUErrieri"
    # contains "RG" and used to price as a five-setter), so slam status comes
    # from the ESPN schedule: is either player in a slam singles draw?
    best_of = 3
    if tour_label == "ATP" and slam_names:
        if _norm(players[0]["name"]) in slam_names or _norm(players[1]["name"]) in slam_names:
            best_of = 5
    # ITF: exact-name match only (no last-name fuzzy) so we never attach a charted
    # ATP/WTA player to an unrelated lower-tier player and invent an edge.
    fuzzy = not tour_label.startswith("ITF")
    ra = td.match_rates(players[0]["name"], surf_key, tcode, fuzzy=fuzzy)
    rb = td.match_rates(players[1]["name"], surf_key, tcode, fuzzy=fuzzy)
    lg = td.league(tcode)

    a = {"name": players[0]["name"], "cents": players[0]["cents"], "ticker": players[0]["ticker"]}
    b = {"name": players[1]["name"], "cents": players[1]["cents"], "ticker": players[1]["ticker"]}
    # recent-load fatigue per player (differential drives the sim)
    fidx = fatigue_idx or {}
    fa = fidx.get(_norm(a["name"]))
    fb = fidx.get(_norm(b["name"]))
    a["fatigue"], b["fatigue"] = fa, fb
    a["n"], b["n"] = (ra[4]["n"] if ra else 0), (rb[4]["n"] if rb else 0)
    # Elo from settled results -- works for everyone, including ITF players the
    # charting never sees. This is the UFC-style "rate them from past matches".
    elo_pa = None
    # Surface-aware when we know the court: the player's overall rating plus their
    # surface DEVIATION, shrunk by how much evidence that surface carries
    # (tennis_elo.K_SURFACE), so a clay specialist is rated as one on clay without
    # discarding what they have done elsewhere. This is the "#1 player can be a dog
    # on clay" effect, and it only became supportable once the deep archive made
    # per-surface samples big enough -- measured, it was harmful on thinner data,
    # and the earlier parallel-chain estimator was harmful even on the deep one.
    ea = tennis_elo.rate(a["name"], tcode, surface)
    eb = tennis_elo.rate(b["name"], tcode, surface)
    if ea and eb:
        elo_pa = round(100.0 * tennis_elo.win_prob(ea["elo"], eb["elo"]), 1)
        a["elo"], b["elo"] = round(ea["elo"]), round(eb["elo"])
        a["elo_n"], b["elo_n"] = ea["n"], eb["n"]
        for p, e in ((a, ea), (b, eb)):
            if e.get("elo_overall") is not None:
                p["elo_overall"] = round(e["elo_overall"])
                p["elo_surface_n"] = e.get("surf_n", 0)
                p["elo_by_surface"] = {k: {"elo": round(v["elo"]), "n": v["n"]}
                                       for k, v in (e.get("surf") or {}).items()}
    # Recent form from settled results. The ESPN fatigue crawl above is ATP/WTA
    # only -- under a tenth of a typical board -- so form for the ITF bulk comes
    # from the same settled-match store the Elo is built on. Context for a human
    # reading the price, deliberately not an input to the probability: see
    # tennis_elo.form for the backtest that rejected wiring it in.
    a["form"] = tennis_elo.form(a["name"], tcode)
    b["form"] = tennis_elo.form(b["name"], tcode)
    elo_n = min((ea or {}).get("n", 0), (eb or {}).get("n", 0)) if (ea and eb) else 0
    elo_ok = elo_pa is not None and elo_n >= _ELO_MIN

    sim = None
    source = "market"
    conf = 0
    live_rates = None
    if ra and rb:
        da = {"spw": ra[0], "rpw": ra[1], "ace": ra[2], "df": ra[3]}
        db = {"spw": rb[0], "rpw": rb[1], "ace": rb[2], "df": rb[3]}
        fpair = ((fa or {}).get("load", 0.0), (fb or {}).get("load", 0.0)) if (fa or fb) else None
        # Matchup nudges the serve/return rates can't see: the lefty-vs-righty
        # familiarity edge, and a hard-shrunk head-to-head. Both are small by
        # design — see tennis_data for why neither is a baseball-sized platoon.
        edge = 0.0
        try:
            profs = ra[4].get("_all") if isinstance(ra[4], dict) else None
            profs = profs or td.profiles(tcode)
            edge = (td.hand_edge((ra[4] or {}).get("hand"), (rb[4] or {}).get("hand"))
                    + td.h2h_edge(profs, a["name"], b["name"]))
        except Exception:
            edge = 0.0
        a["hand"], b["hand"] = (ra[4] or {}).get("hand"), (rb[4] or {}).get("hand")
        a["matchup_edge"] = round(edge * 100, 2)
        b["matchup_edge"] = round(-edge * 100, 2)
        # Kept so the live board can compute an in-match win prob from the score.
        live_rates = {"da": da, "db": db, "lg": lg, "best_of": best_of,
                      "fatigue": fpair, "edge": edge}
        sim = ts.simulate(da, db, lg, best_of=best_of, n=n_sims, fatigue=fpair, edge=edge)
        a["hold"], b["hold"] = sim["holdA"], sim["holdB"]
        a["prof"], b["prof"] = ra[4], rb[4]
        if elo_ok:                              # ensemble the two independent reads
            # The serve sim's share of the WIN probability, scaled by how much
            # charting stands behind the thinner of the two players.
            #
            # This was a flat 0.55, chosen when the Elo was two months of Kalshi
            # results and the weaker half. With 20 years of ATP history behind it
            # that is now backwards, and three independent checks agree: on a
            # point-in-time walk of the charting archive the held-out log loss
            # falls monotonically as serve weight drops (0.6273 at 0.55 vs 0.5647
            # at zero); against an 8-book consensus the PRODUCTION serve sim sits
            # 18.0pp out while the Elo sits 10.7pp; and depth-scaled variants beat
            # every flat weight above 0.15.
            #
            # The scaling is what makes a low weight safe rather than blunt. The
            # serve model's failure mode is thin charting: rates shrink to the
            # league average, so a junior with 0.2 charted matches came out at 36%
            # against de Minaur (books: 10.7%). Weighting by depth gives that
            # match essentially pure Elo while leaving a well-charted pair with a
            # real serve contribution.
            #
            # Kept deliberately conservative rather than following the fit to
            # zero: the backtest's serve model is a simplified one (no surface
            # split, fixed ace/df, no handedness or H2H), so it understates what
            # ships here. The derived markets -- games, sets, aces, straight sets
            # -- still come entirely from the sim and are unaffected.
            depth = min(a["n"] or 0.0, b["n"] or 0.0)
            w_serve = _SERVE_CAP * depth / (depth + _SERVE_K)
            model_pa = round(w_serve * sim["p_a"] + (1 - w_serve) * elo_pa, 1)
            a["serve_weight"] = b["serve_weight"] = round(w_serve, 3)
            source = "serve+elo"
            # Confidence in an ENSEMBLE is bounded by its weakest component, not
            # its strongest. This was max(), which was survivable while the Elo
            # was as thin as the charting; once tennis_history gave ATP players
            # hundreds of rated matches it broke badly -- elo_n of 339 drove the
            # market weight to 0.96, so a deep Elo was used to justify trusting a
            # SERVE sim that had not improved at all. Measured against an 8-book
            # consensus, that pushed our error on the worst match from 16.9pp to
            # 21.7pp: more data, worse answer.
            conf = min(min(a["n"], b["n"]), elo_n)
            # Two independent reads disagreeing is evidence that one of them is
            # wrong, and we do not know which. Fade toward the market when they
            # do: full confidence when they agree, a quarter of it once they are
            # 30 points apart.
            spread = abs(sim["p_a"] - elo_pa)
            conf *= max(0.25, 1.0 - spread / 40.0)
        else:
            model_pa = sim["p_a"]
            source = "serve"
            conf = min(a["n"], b["n"])
        a["model_win"], b["model_win"] = model_pa, round(100 - model_pa, 1)
    elif elo_ok:                                # no charting, but Elo has them
        a["model_win"], b["model_win"] = elo_pa, round(100 - elo_pa, 1)
        source = "elo"
        conf = elo_n
    else:
        a["model_win"] = b["model_win"] = None

    # de-vig market + confidence blend (thin sample -> defer to market)
    ca, cb = a["cents"], b["cents"]
    mkt_a = mkt_b = None
    if ca is not None and cb is not None and (ca + cb) > 0:
        mkt_a = round(100.0 * ca / (ca + cb), 1)
        mkt_b = round(100.0 * cb / (ca + cb), 1)
    # Overround (vig). A no-liquidity pre-match book quotes both sides high (e.g.
    # 94/94 -> overround ~88); a real two-sided market is ~100-110. We treat a
    # huge overround as "not really priced" and keep it out of the board/combos.
    overround = (ca + cb - 100) if (ca is not None and cb is not None) else None
    tradeable = overround is not None and overround <= 25
    w = conf / (conf + _BLEND_K.get(source, 12.0))
    for p, mk in ((a, mkt_a), (b, mkt_b)):
        p["mkt_win"] = mk
        p["confidence"] = round(w, 2)
        if p["model_win"] is None:
            p["fair_win"] = mk
            p["edge"] = None
        elif mk is None:
            p["fair_win"] = p["model_win"]
            p["edge"] = None
        else:
            fair = round(w * p["model_win"] + (1 - w) * mk, 1)
            p["fair_win"] = fair
            p["edge"] = round(fair - p["cents"], 1) if p["cents"] is not None else None
            # Elo-only on a lopsided, liquid market: the market is near-certain and
            # our thin Elo can't beat it -- never let it manufacture a longshot edge.
            if source == "elo" and p["cents"] is not None and not (12 <= p["cents"] <= 88):
                p["edge"] = None

    # Reality-calibrate fair_win against graded tennis outcomes (the prediction
    # logger). We keep the RAW value for the logger (calibration is fit on raw, so
    # calibrating what we log would double-count), overwrite fair_win with the
    # corrected number for display/edges, and recompute the edge from it. No-op
    # (T=1.0) until enough tennis markets have settled.
    try:
        import calibrate
        for p in (a, b):
            fw = p.get("fair_win")
            if fw is None:
                continue
            p["fair_win_raw"] = fw
            cal = round(100 * calibrate.tennis(fw / 100.0), 1)
            p["fair_win"] = cal
            if p.get("edge") is not None and p.get("cents") is not None:
                p["edge"] = round(cal - p["cents"], 1)
    except Exception:
        pass

    # Confidence tier (how much backs the read) so the user knows what to trust.
    # serve charting -> high/medium/thin; Elo-only -> "elo"; nothing -> "market".
    modeled = a["model_win"] is not None
    if not modeled:
        tier = "market"
    elif source == "elo":
        tier = "elo"
    else:
        cn = min(a["n"], b["n"])
        tier = "high" if cn >= 40 else "medium" if cn >= 12 else "thin"
    # The single "lean": the side with the best positive edge, discounted by how
    # thin the charting is. play_strength sorts the board so trustworthy edges
    # rise above thin-data mirages.
    lean = None
    best_p = None
    for p in (a, b):
        if p.get("edge") is not None and p["edge"] > 0:
            if best_p is None or p["edge"] > best_p["edge"]:
                best_p = p
    if best_p:
        strength = round(best_p["edge"] * w, 1)        # edge x confidence weight
        lean = {"pick": best_p["name"], "fair_win": best_p["fair_win"],
                "cents": best_p["cents"], "edge": best_p["edge"], "strength": strength}

    # Where to find it on Kalshi: the tour is the tab (ATP / WTA / ITF), the
    # tournament (host city, or the ITF tier-city from the title) is the
    # sub-tab — exactly how Kalshi navigates (ATP → Gstaad, WTA → Athens).
    kalshi_series = {"ATP": "ATP", "WTA": "WTA",
                     "ITF": "ITF", "ITF-W": "ITF Women"}.get(tour_label, "Tennis")
    match = {"event": ev, "tour": tour_label, "date": date, "start": start,
             # None when the stop couldn't be identified -- say so rather than
             # printing a confident "Hard" the model isn't actually using.
             "surface": surface or "Unknown",
             "surface_known": surface is not None,
             "best_of": best_of, "a": a, "b": b, "conf_tier": tier, "modeled": modeled,
             "model_source": source, "tradeable": tradeable, "overround": overround,
             "tournament": tournament, "kalshi_series": kalshi_series,
             "live_rates": live_rates,
             "lean": lean, "play_strength": (lean or {}).get("strength", 0)}
    insights = _insights(a, b, sim, surface,
                         trusted=tier in ("high", "medium")) if sim else []
    # Elo angle (works with or without the serve sim)
    if "elo" in source and a.get("elo") and b.get("elo"):
        hi, lo = (a, b) if a["elo"] >= b["elo"] else (b, a)
        if abs(a["elo"] - b["elo"]) >= 25:
            insights.append(f"📊 Elo favors {hi['name'].split()[-1]} ({hi['elo']} vs {lo['elo']}, from {min(a.get('elo_n', 0), b.get('elo_n', 0))}+ recent results)")
    if sim:
        match.update({
            "mean_games": sim["mean_games"], "games_ladder": sim["games_ladder"],
            "total_sets": sim["total_sets"], "a_straight": sim["a_straight"],
            "aces_total": sim["aces_total"], "aces_a": sim["aces_a"], "aces_b": sim["aces_b"],
            "set_winners": {k: v for k, v in sim.items() if k.startswith("set")}})
    match["insights"] = insights[:6]
    # strip non-serializable / heavy bits before returning
    for p in (a, b):
        p.pop("prof", None)
    return match


def _compute(n_sims=12000):
    fatigue_idx = _fatigue_index()
    try:
        import tennis_live
        slam_names = tennis_live.slam_singles_names()
        surface_map = tennis_live.surface_map()
        tourn_map = tennis_live.tournament_map()
        start_map = tennis_live.start_map()
    except Exception:
        slam_names = set()
        surface_map = {}
        tourn_map = {}
        start_map = {}
    # Gather every tournament on the board first, so surfaces the keyword map
    # cannot name are resolved in ONE batched pass with a spend cap, instead of
    # each match firing its own lookup. Surface belongs to a venue, so these are
    # cached permanently and a steady board costs nothing after the first run.
    slate = []
    for label, series, tcode in _TOURS:
        for ev, rec in _match_markets(series).items():
            players = rec["players"] if isinstance(rec, dict) else rec
            tournament = rec.get("tournament") if isinstance(rec, dict) else None
            slate.append((label, series, tcode, ev, players, tournament))
    serp_surfaces = {}
    try:
        import serp_surface
        # ITF stops only. A tour event falls through to Hard by construction (see
        # _build_match), and its bare Kalshi name is too generic to search well
        # anyway -- "Washington tennis tournament court surface" came back split
        # 5-5 between clay and hard. Spending the quota there would buy noise.
        unknown = {t for label, _, _, _, _, t in slate
                   if t and label.startswith("ITF") and not _surface_of(t)}
        if unknown:
            serp_surfaces = serp_surface.resolve(sorted(unknown))
    except Exception:
        serp_surfaces = {}

    matches = []
    for label, series, tcode, ev, players, tournament in slate:
            try:
                m = _build_match(label, ev, players, n_sims, fatigue_idx, tcode,
                                 slam_names=slam_names, tournament=tournament,
                                 surface_map=surface_map, tourn_map=tourn_map,
                                 start_map=start_map, serp_surfaces=serp_surfaces)
            except Exception:
                m = None
            if m:
                matches.append(m)
    # Show EVERY match on Kalshi, not just the ones we model — the user needs to
    # find them all. A match is "live" (real two-sided market) or "unopened" (a
    # wide pre-match book: both sides quoted high, no liquidity yet). The tier
    # flag lets the UI keep unopened matches visible but clearly labeled and
    # sorted below the ones with a real read.
    for m in matches:
        m["tier"] = ("play" if (m.get("modeled") or m.get("tradeable"))
                     else "unopened")
    # Sort: real reads first (by play strength), unopened at the bottom
    # (alphabetical by tournament so they're findable).
    matches.sort(key=lambda m: (
        0 if m["tier"] == "play" else 1,
        -(m.get("play_strength", 0)) if m["tier"] == "play" else 0,
        (m.get("tournament") or "zzz"), m["a"]["name"]))
    return {"sport": "tennis", "generated": clock.now_et().isoformat(timespec="seconds"),
            "n_matches": len(matches),
            "n_play": sum(1 for m in matches if m["tier"] == "play"),
            "matches": matches}


def board():
    """Cached tennis board. Non-blocking: returns the cached board if fresh, else
    kicks off a background compute and returns None until ready."""
    key = ("tennis_board",)
    hit = _form.get(key)
    if hit and (time.time() - hit[0]) < 1200 and hit[1] is not None:
        return hit[1]
    if not _inflight.get("b"):
        _inflight["b"] = True

        def _bg():
            try:
                racing._cached(key, 1200, _compute)
            finally:
                _inflight["b"] = False
        threading.Thread(target=_bg, daemon=True).start()
    return hit[1] if hit else None
