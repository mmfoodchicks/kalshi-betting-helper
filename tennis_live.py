"""Live tennis from ESPN's scoreboards: who's on court right now, set-by-set
scores, and which tournament each player is actually in.

Three consumers:
  - the Sports → Live tab (matches in progress with real scores),
  - the tennis board's live merge (LIVE chip + the "lopsided" upset radar:
    a big-Elo favorite who just dropped a set is exactly when Kalshi's
    sentiment-driven prices overshoot),
  - best-of detection: the Kalshi ticker is [date][3 letters per player] and
    carries NO tournament, so "is this a Grand Slam" (best-of-5 for the men)
    can only come from a real schedule source. ESPN's event name is that
    source; the old ticker-substring hints false-fired on player letters
    (NARdi/GUErrieri contains "RG" -> priced as a five-setter).
"""
import datetime
import unicodedata

import clock
import racing

_SLAMS = ("wimbledon", "us open", "australian open", "french open", "roland garros")

# Court surface by tournament/city keyword. Surface is a STABLE property of each
# stop, so a keyword map beats guessing from the calendar — which can't tell
# post-Wimbledon clay (Bastad/Gstaad/Umag/Hamburg) from the concurrent US hard
# swing, since they run at the same time. Everything unmatched defaults to Hard
# (the tour's most common surface); the calendar heuristic is the final fallback.
_GRASS_KW = ("wimbledon", "halle", "queen", "queens", "eastbourne", "newport",
             "mallorca", "stuttgart", "hertogenbosch", "bois-le-duc", "libema",
             "nottingham", "birmingham", "berlin", "bad homburg")
_CLAY_KW = ("roland garros", "french open", "monte carlo", "monte-carlo", "madrid",
            "rome", "roma", "barcelona", "hamburg", "bastad", "gstaad", "umag",
            "kitzbuhel", "bucharest", "palermo", "estoril", "munich", "munchen",
            "geneva", "geneve", "lyon", "houston", "marrakech", "cordoba",
            "buenos aires", "rio", "santiago", "iasi", "prague", "warsaw",
            "bogota", "parma", "cagliari", "belgrade", "sardegna", "olomouc",
            "porto", "nordea", "swiss open", "croatia open",
            # ITF stops. These matter far more than the tour list: ITF is over 90%
            # of a typical Kalshi tennis board, and until the ITF path started
            # consulting this map (see tennis_prices._surface_of) every one of them
            # fell through to a blind "Hard".
            #
            # Kept deliberately SHORT. Surface is a property of a specific venue,
            # not of a city, and no free source we can reach publishes it for the
            # ITF calendar (the ITF site is bot-walled, Sofascore 403s, ESPN carries
            # no surface field and no ITF). Guessing a long list of host towns would
            # just reintroduce confident-but-wrong labels in a new place. Anything
            # absent here resolves to unknown, which tennis_prices now models
            # surface-agnostically off the player's overall profile -- the right
            # answer when we don't know, and the reason this list can stay small.
            "villa constitucion", "castelo branco", "koszalin", "hechingen",
            "kursumlijska banja", "santa margherita", "cordenons")
# US/indoor/AO hard swings are the default, so no explicit HARD list is needed.


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join("".join(c for c in s.lower() if c.isalnum() or c == " ").split())


def surface_of(tournament, venue_city=None):
    """Court surface ('Clay'|'Grass'|'Hard'|None) from the tournament name / host
    city keyword. None when nothing matches (caller falls back to the calendar)."""
    hay = f"{tournament or ''} {venue_city or ''}".lower()
    if any(k in hay for k in _GRASS_KW):
        return "Grass"
    if any(k in hay for k in _CLAY_KW):
        return "Clay"
    return None


def _board(tour, ds):
    try:
        return racing._get_json(
            f"https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard?dates={ds}",
            timeout=12)
    except Exception:
        return None


def _set_state(games_a, games_b):
    """('a'|'b'|None) — who won a completed set, or None if it's still going."""
    hi, lo = max(games_a, games_b), min(games_a, games_b)
    done = (hi >= 6 and hi - lo >= 2) or hi == 7
    if not done:
        return None
    return "a" if games_a > games_b else "b"


def _parse_day(d, singles_only=True):
    """One scoreboard day -> [{tournament, slam, draw, state, detail, a, b,
    sets_a, sets_b, cur, score}] for singles matches."""
    out = []
    for e in (d or {}).get("events", []):
        tourn = e.get("name") or ""
        slam = any(s in tourn.lower() for s in _SLAMS)
        ev_venue = e.get("venue") or {}
        g_venue = (((e.get("groupings") or [{}])[0].get("competitions") or [{}])[0]
                   .get("venue") or {})
        venue = (ev_venue.get("displayName") or ev_venue.get("fullName")
                 or g_venue.get("fullName") or g_venue.get("displayName") or "")
        surface = surface_of(tourn, venue)
        # Kalshi labels tennis tabs by CITY (ATP Bastad, WTA Athens), not the
        # sponsor name (Nordea Open). The ESPN venue is "City, Country" — take
        # the city (ASCII, as Kalshi writes it: Bastad not Båstad) so a combo
        # pin points at the exact Kalshi tab.
        raw_city = venue.split(",")[0].strip() if venue else ""
        city = (unicodedata.normalize("NFKD", raw_city).encode("ascii", "ignore")
                .decode().strip() or None) if raw_city else None
        for g in (e.get("groupings") or []):
            draw = ((g.get("grouping") or {}).get("displayName") or "")
            if singles_only and "singles" not in draw.lower():
                continue
            for c in (g.get("competitions") or []):
                st = ((c.get("status") or {}).get("type") or {})
                comps = c.get("competitors") or []
                if len(comps) != 2:
                    continue
                names = [((x.get("athlete") or {}).get("displayName") or "") for x in comps]
                if not names[0] or not names[1]:
                    continue
                lines = [[int(s.get("value") or 0) for s in (x.get("linescores") or [])]
                         for x in comps]
                n_sets = max(len(lines[0]), len(lines[1]))
                sets_a = sets_b = 0
                cur = None
                pairs = []
                for i in range(n_sets):
                    ga = lines[0][i] if i < len(lines[0]) else 0
                    gb = lines[1][i] if i < len(lines[1]) else 0
                    pairs.append(f"{ga}-{gb}")
                    w = _set_state(ga, gb)
                    if w == "a":
                        sets_a += 1
                    elif w == "b":
                        sets_b += 1
                    else:
                        cur = (ga, gb)
                out.append({
                    "tournament": tourn, "slam": slam, "draw": draw,
                    "surface": surface, "city": city,
                    # ISO start time (per-match if ESPN gives one, else the event's).
                    "start": c.get("date") or e.get("date"),
                    "state": st.get("state"), "detail": st.get("shortDetail") or "",
                    "a": names[0], "b": names[1],
                    "na": _norm(names[0]), "nb": _norm(names[1]),
                    "sets_a": sets_a, "sets_b": sets_b,
                    "cur": cur, "score": " ".join(pairs)})
    return out


def snapshot():
    """All of today's singles matches (both tours), cached 60s — fast-moving."""
    def build():
        ds = clock.today_et().strftime("%Y%m%d")
        rows = []
        for tour in ("atp", "wta"):
            rows.extend(_parse_day(_board(tour, ds)))
        # both feeds can carry the same tournament; dedup by player pair
        seen, out = set(), []
        for r in rows:
            key = frozenset((r["na"], r["nb"]))
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        return out
    return racing._cached(("tennis_live_snap",), 60, build) or []


def slam_singles_names():
    """Normalized names of every singles player in a Grand Slam draw today or
    tomorrow — drives best-of-5 for the men. Cached an hour."""
    def build():
        names = set()
        today = clock.today_et()
        for off in (0, 1):
            ds = (today + datetime.timedelta(days=off)).strftime("%Y%m%d")
            for tour in ("atp", "wta"):
                for r in _parse_day(_board(tour, ds)):
                    # Qualifying at a slam is still best-of-3 — main draw only.
                    if r["slam"] and "qualif" not in r["draw"].lower():
                        names.add(r["na"])
                        names.add(r["nb"])
        return names
    return racing._cached(("tennis_slam_names",), 3600, build) or set()


def surface_map():
    """{normalized player name: 'Clay'|'Grass'|'Hard'} for every singles player
    with a match today or tomorrow, from the real tournament (not the calendar).
    Cached an hour. Only players whose tournament matched a surface keyword are
    included — the board falls back to the calendar for anyone absent."""
    def build():
        out = {}
        today = clock.today_et()
        for off in (0, 1):
            ds = (today + datetime.timedelta(days=off)).strftime("%Y%m%d")
            for tour in ("atp", "wta"):
                for r in _parse_day(_board(tour, ds)):
                    if r.get("surface"):
                        out[r["na"]] = r["surface"]
                        out[r["nb"]] = r["surface"]
        return out
    return racing._cached(("tennis_surface_map",), 3600, build) or {}


def tournament_map():
    """{normalized player name: host city} for singles players today/tomorrow —
    the Kalshi tab label (ATP Bastad, WTA Athens). Fills the tournament for
    main-tour matches whose Kalshi title carries only the round, so a combo pin
    can point at the exact Kalshi location. Cached an hour."""
    def build():
        out = {}
        today = clock.today_et()
        for off in (0, 1):
            ds = (today + datetime.timedelta(days=off)).strftime("%Y%m%d")
            for tour in ("atp", "wta"):
                for r in _parse_day(_board(tour, ds)):
                    if r.get("city"):
                        out.setdefault(r["na"], r["city"])
                        out.setdefault(r["nb"], r["city"])
        return out
    return racing._cached(("tennis_tourn_map",), 3600, build) or {}


def start_map():
    """{normalized player name: ISO start time} for singles players today/
    tomorrow, so a match tile can show when it's scheduled to begin. Only
    not-yet-final matches carry a meaningful start. Cached 5 min."""
    def build():
        out = {}
        today = clock.today_et()
        for off in (0, 1):
            ds = (today + datetime.timedelta(days=off)).strftime("%Y%m%d")
            for tour in ("atp", "wta"):
                for r in _parse_day(_board(tour, ds)):
                    if r.get("start") and r.get("state") != "post":
                        out.setdefault(r["na"], r["start"])
                        out.setdefault(r["nb"], r["start"])
        return out
    return racing._cached(("tennis_start_map",), 300, build) or {}


def live_rows():
    """Matches in progress right now, shaped for the Sports → Live tab."""
    out = []
    for r in snapshot():
        if r["state"] != "in":
            continue
        cur = f" ({r['cur'][0]}-{r['cur'][1]})" if r["cur"] else ""
        out.append({
            "sport": "🎾 Tennis", "confirmed": True,
            "title": f"{r['a']} vs {r['b']}",
            "score": f"{r['sets_a']}–{r['sets_b']} sets{cur}",
            "detail": f"{r['tournament']} · {r['detail']}".strip(" ·"),
            "nav": {"tab": "tennis", "q": r["a"]},
        })
    return out


def attach(board):
    """Merge the live snapshot onto a (cached) tennis board WITHOUT mutating the
    cache: returns a new payload where each in-progress match carries
    m['live'] = {detail, score, sets, tournament} and, when a big favorite is
    trailing, m['upset'] = {fav, gap, note} — the lopsided radar. Sorting keys
    ride along so the frontend can put the alarms on top."""
    if not board:
        return board
    idx = {}
    for r in snapshot():
        if r["state"] == "in":
            idx[frozenset((r["na"], r["nb"]))] = r
    matches = []
    for m in board.get("matches") or []:
        m = dict(m)
        r = idx.get(frozenset((_norm(m["a"]["name"]), _norm(m["b"]["name"]))))
        if r:
            aligned = _norm(m["a"]["name"]) == r["na"]
            sa, sb = (r["sets_a"], r["sets_b"]) if aligned else (r["sets_b"], r["sets_a"])
            cur = r["cur"] if aligned or not r["cur"] else (r["cur"][1], r["cur"][0])
            m["live"] = {"detail": r["detail"], "tournament": r["tournament"],
                         "score": r["score"], "sets_a": sa, "sets_b": sb,
                         "cur": list(cur) if cur else None}
            # Live in-match win probability from the CURRENT score: re-run the
            # point-by-point sim from here. This is the real number behind the
            # upset radar — is the favorite still 85% after dropping a set, or has
            # it genuinely swung? Only when we have the serve model (live_rates).
            lr = m.get("live_rates")
            if lr:
                try:
                    import tennis_sim as _ts
                    ga, gb = (cur if cur else (0, 0))
                    pa_live = _ts.live_winprob(
                        lr["da"], lr["db"], lr["lg"], lr["best_of"],
                        sa, sb, ga, gb, n=3000, fatigue=lr.get("fatigue"))
                    m["live"]["p_a"] = pa_live
                    m["live"]["p_b"] = round(100.0 - pa_live, 1)
                    # Live edge vs the current Kalshi ask, per player.
                    for side, plive in (("a", pa_live), ("b", 100.0 - pa_live)):
                        c = m[side].get("cents")
                        m["live"][f"edge_{side}"] = round(plive - c, 1) if c is not None else None
                except Exception:
                    pass
            # Lopsided radar: the higher-Elo (fallback: higher fair-win) player
            # is behind. Kalshi over-reacts to a dropped set on a big name —
            # that's the moment the favorite's price is cheapest.
            ea, eb = m["a"].get("elo"), m["b"].get("elo")
            if ea and eb:
                fav, dog = (m["a"], m["b"]) if ea >= eb else (m["b"], m["a"])
                gap = abs(ea - eb)
            else:
                fa = m["a"].get("fair_win") or 0
                fb = m["b"].get("fair_win") or 0
                fav, dog = (m["a"], m["b"]) if fa >= fb else (m["b"], m["a"])
                gap = round(abs(fa - fb) * 8)      # ~fair-win gap on an Elo-ish scale
            fav_sets, dog_sets = (sa, sb) if fav is m["a"] else (sb, sa)
            behind_sets = fav_sets < dog_sets
            behind_cur = False
            fg = dg = None
            if cur and fav_sets == dog_sets:
                fg, dg = (cur[0], cur[1]) if fav is m["a"] else (cur[1], cur[0])
                behind_cur = (dg - fg) >= 2
            if (behind_sets or behind_cur) and gap >= 40:
                note = ("down a set" if behind_sets else
                        f"behind {fg}-{dg} in this set")
                # Prefer the real live win prob for the favorite when we have it —
                # "was 90% pre-match, still 71% live" is far better than a gap
                # heuristic, and it directly frames the value vs the live price.
                fav_live = None
                lp = m.get("live") or {}
                if lp.get("p_a") is not None:
                    fav_live = lp["p_a"] if fav is m["a"] else lp["p_b"]
                m["upset"] = {"fav": fav["name"], "fav_cents": fav.get("cents"),
                              "gap": gap, "note": note,
                              "sets": f"{fav_sets}–{dog_sets}",
                              "fav_live_pct": fav_live}
                # Rank alarms by how underpriced the favorite is live (live% minus
                # ask), falling back to the Elo gap when unpriced.
                m["upset_score"] = (round(fav_live - (fav.get("cents") or fav_live))
                                    if fav_live is not None and fav.get("cents") is not None
                                    else gap)
        m.pop("live_rates", None)       # heavy; never ship to the client
        matches.append(m)
    out = dict(board)
    out["matches"] = matches
    out["n_live"] = sum(1 for m in matches if m.get("live"))
    out["n_upsets"] = sum(1 for m in matches if m.get("upset"))
    return out
