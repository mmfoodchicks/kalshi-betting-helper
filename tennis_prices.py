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
import threading
import time
import unicodedata

import kalshi
import racing
import tennis_data as td
import tennis_sim as ts

_TOURS = {"m": ("ATP", "KXATPMATCH"), "w": ("WTA", "KXWTAMATCH")}
_form = racing._form_cache
_inflight = {}


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join("".join(c for c in s.lower() if c.isalnum() or c == " ").split())


def _surface_for(date):
    """Infer court surface from the tennis calendar (the match markets don't carry
    it). Clay spring, grass for the ~5 weeks into mid-July, hard the rest."""
    try:
        d = datetime.date(int(date[:4]), int(date[4:6]), int(date[6:8]))
    except Exception:
        d = datetime.date.today()
    md = (d.month, d.day)
    if (4, 1) <= md < (6, 9):
        return "Clay"
    if (6, 9) <= md < (7, 20):
        return "Grass"
    return "Hard"


def _match_markets(series):
    """Open winner markets grouped by event -> {event: [{name, cents, ticker}]}."""
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
            out.setdefault(ev, []).append({
                "name": nm, "ticker": tk,
                "cents": kalshi._cents(m.get("yes_ask_dollars"))})
        cursor = d.get("cursor") or ""
        if not cursor:
            break
    return out


def _event_date(ev):
    """KXATPMATCH-26JUN26ABCDEF... -> '20260626' (best effort)."""
    import re
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", ev or "")
    if not m:
        return datetime.date.today().strftime("%Y%m%d")
    yy, mon, dd = m.groups()
    months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
              "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
    return f"20{yy}{months.get(mon, 1):02d}{dd}"


def _insights(a, b, sim, surface):
    """Plain-English angles from the model numbers."""
    out = []
    fav, dog = (a, b) if a["model_win"] >= b["model_win"] else (b, a)
    # serve dominance
    if abs(sim["holdA"] - sim["holdB"]) >= 6:
        s = a if sim["holdA"] > sim["holdB"] else b
        out.append(f"🎾 {s['name'].split()[-1]} serves bigger (holds {max(sim['holdA'], sim['holdB'])}% vs {min(sim['holdA'], sim['holdB'])}%)")
    # surface edge: player's surface serve rate well above their overall
    for p in (a, b):
        prof = p.get("prof")
        if not prof:
            continue
        ov = prof["overall"]["spw"]
        sv = (prof.get("surf") or {}).get(surface, {}).get("spw", ov)
        if sv - ov >= 0.02 and (prof.get("surf") or {}).get(surface, {}).get("w", 0) >= 5:
            out.append(f"📈 {p['name'].split()[-1]} is stronger on {surface.lower()}")
    # straight-set likelihood (only meaningful for the favorite)
    if a["model_win"] >= b["model_win"] and sim["a_straight"] >= 52:
        out.append(f"🧹 {a['name'].split()[-1]} often in straight sets ({sim['a_straight']}%)")
    # tight match -> over games
    if 42 <= fav["model_win"] <= 58:
        out.append(f"⚖️ Coin-flip — lean Over {sim['mean_games']:.0f} games / 3 sets")
    elif fav["model_win"] >= 72:
        out.append(f"💪 {fav['name'].split()[-1]} a strong favorite ({fav['model_win']}%)")
    # model vs market value
    for p in (a, b):
        if p.get("edge") is not None and p["edge"] >= 6:
            out.append(f"💰 Value: model has {p['name'].split()[-1]} at {p['fair_win']}% vs {p['cents']}¢")
    # ace-heavy
    if sim["aces_total"] >= 18:
        out.append(f"🔥 Big-serving match (~{sim['aces_total']:.0f} aces) — ace overs in play")
    return out[:5]


def _build_match(tour_label, ev, players, n_sims):
    if len(players) != 2:
        return None
    date = _event_date(ev)
    surface = _surface_for(date)
    best_of = 5 if (tour_label == "ATP" and _is_slam(ev)) else 3
    tcode = "m" if tour_label == "ATP" else "w"
    ra = td.match_rates(players[0]["name"], surface, tcode)
    rb = td.match_rates(players[1]["name"], surface, tcode)
    lg = td.league(tcode)

    a = {"name": players[0]["name"], "cents": players[0]["cents"], "ticker": players[0]["ticker"]}
    b = {"name": players[1]["name"], "cents": players[1]["cents"], "ticker": players[1]["ticker"]}
    sim = None
    if ra and rb:
        da = {"spw": ra[0], "rpw": ra[1], "ace": ra[2], "df": ra[3]}
        db = {"spw": rb[0], "rpw": rb[1], "ace": rb[2], "df": rb[3]}
        sim = ts.simulate(da, db, lg, best_of=best_of, n=n_sims)
        a["model_win"], b["model_win"] = sim["p_a"], sim["p_b"]
        a["hold"], b["hold"] = sim["holdA"], sim["holdB"]
        a["prof"], b["prof"] = ra[4], rb[4]
        a["n"], b["n"] = ra[4]["n"], rb[4]["n"]
    else:
        # no profile for one side -> lean fully on the market later
        a["model_win"] = b["model_win"] = None
        a["n"] = b["n"] = 0

    # de-vig market + confidence blend (thin charting -> defer to market)
    ca, cb = a["cents"], b["cents"]
    mkt_a = mkt_b = None
    if ca is not None and cb is not None and (ca + cb) > 0:
        mkt_a = round(100.0 * ca / (ca + cb), 1)
        mkt_b = round(100.0 * cb / (ca + cb), 1)
    conf = min(a["n"], b["n"])
    w = conf / (conf + 12.0)
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

    match = {"event": ev, "tour": tour_label, "date": date, "surface": surface,
             "best_of": best_of, "a": a, "b": b}
    if sim:
        match.update({
            "mean_games": sim["mean_games"], "games_ladder": sim["games_ladder"],
            "total_sets": sim["total_sets"], "a_straight": sim["a_straight"],
            "aces_total": sim["aces_total"], "aces_a": sim["aces_a"], "aces_b": sim["aces_b"],
            "set_winners": {k: v for k, v in sim.items() if k.startswith("set")},
            "insights": _insights(a, b, sim, surface)})
    # strip non-serializable / heavy bits before returning
    for p in (a, b):
        p.pop("prof", None)
    return match


_SLAM_HINTS = ("WIM", "USO", "RG", "FREN", "AUS", "AO", "ROLAND", "OPEN")


def _is_slam(ev):
    e = (ev or "").upper()
    return any(h in e for h in _SLAM_HINTS)


def _compute(n_sims=12000):
    matches = []
    for tcode, (label, series) in _TOURS.items():
        evs = _match_markets(series)
        for ev, players in evs.items():
            try:
                m = _build_match(label, ev, players, n_sims)
            except Exception:
                m = None
            if m:
                matches.append(m)
    # sort: biggest model-vs-market edge first, then by confidence
    def keyfn(m):
        edges = [p.get("edge") or -99 for p in (m["a"], m["b"])]
        return (max(edges), m["a"].get("confidence", 0))
    matches.sort(key=keyfn, reverse=True)
    return {"sport": "tennis", "generated": datetime.datetime.utcnow().isoformat() + "Z",
            "n_matches": len(matches), "matches": matches}


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
