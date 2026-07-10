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


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join("".join(c for c in s.lower() if c.isalnum() or c == " ").split())


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
                m["upset"] = {"fav": fav["name"], "fav_cents": fav.get("cents"),
                              "gap": gap, "note": note,
                              "sets": f"{fav_sets}–{dog_sets}"}
                m["upset_score"] = gap
        matches.append(m)
    out = dict(board)
    out["matches"] = matches
    out["n_live"] = sum(1 for m in matches if m.get("live"))
    out["n_upsets"] = sum(1 for m in matches if m.get("upset"))
    return out
