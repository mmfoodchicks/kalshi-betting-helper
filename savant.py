"""Statcast (Baseball Savant) data — the real quality-of-contact and speed
numbers behind a hitter's true talent. These are the actual stats MLB The Show's
ratings are derived from, used directly instead of someone's 0-99 guess:

  - expected stats (xBA / xSLG / xwOBA): results with luck stripped out, for
    regressing a hitter toward what he's genuinely hitting (like FIP for pitchers).
  - sprint speed (ft/sec): drives baserunning + stolen bases in the game sim.

Everything is cached for hours and degrades to {} on any failure, so the model
falls straight back to plain MLB-API stats if Savant is unreachable.
"""

import csv
import io
import urllib.request

import ttlcache

_UA = "kalshi-betting-helper/1.0"
_cache = {}


def _get_csv(url, timeout=20, keep=None):
    """Fetch a CSV as row dicts. `keep`: keep ONLY these columns per row —
    a statcast_search row carries ~90 columns and a batter's season is 1,400
    rows; parsing all of it as string dicts is several MB of transient garbage
    per player, multiplied by eight worker threads. Prune at parse."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode("utf-8-sig")
    rows = csv.DictReader(io.StringIO(text))
    if keep:
        return [{k: r.get(k) for k in keep} for r in rows]
    return list(rows)


def _cached(key, ttl, fn):
    # Sweep-on-insert (ttlcache): the velocity flag keys on (pitcher, DATE)
    # and the x-splits on (batter, season) -- growing keys that the old
    # read-only TTL check kept forever, in every worker.
    return ttlcache.cached(_cache, key, ttl, fn)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def park_factors(season):
    """{home team id: {"runs": mult, "hr": mult}} — Statcast's MEASURED park
    factors (3-year rolling, PA-weighted, venue-conditioned), replacing the
    hand-eyeballed table this app shipped with. The gap was real money: the
    old table had Dodger Stadium run-SUPPRESSING (0.97) where Statcast
    measures it 102 runs / 127 HR single-year — one of the best HR parks in
    baseball, discounted on every HR ladder — and carried Coors at 1.15
    against a measured 1.25.

    The leaderboard page embeds its data as a JS array (the csv param serves
    HTML), so this parses the `data = [...]` blob. Values are indexes around
    100. None on any failure — the caller keeps its static fallback table."""
    def build():
        import json
        import re
        url = ("https://baseballsavant.mlb.com/leaderboard/statcast-park-factors"
               f"?type=year&year={season}&rolling=3")
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                          "AppleWebKit/605.1.15"})
        with urllib.request.urlopen(req, timeout=25) as r:
            body = r.read().decode("utf-8", "replace")
        m = re.search(r"data\s*=\s*(\[.*?\]);", body, re.S)
        if not m:
            return None
        rows = json.loads(m.group(1))
        # Club nickname ("Dodgers") -> team id, from the MLB teams API.
        treq = urllib.request.Request(
            f"https://statsapi.mlb.com/api/v1/teams?sportId=1&season={season}",
            headers={"User-Agent": _UA})
        with urllib.request.urlopen(treq, timeout=20) as r:
            teams = json.loads(r.read().decode())
        id_of = {t.get("teamName"): t["id"] for t in teams.get("teams", [])}
        out = {}
        for row in rows:
            tid = id_of.get(row.get("name_display_club"))
            runs, hr = _f(row.get("index_runs")), _f(row.get("index_hr"))
            if tid and runs:
                out[tid] = {"runs": round(runs / 100.0, 3),
                            "hr": round((hr or runs) / 100.0, 3)}
        return out if len(out) >= 20 else None
    return _cached(("park_factors", season), 24 * 3600, build)


def expected_stats(season):
    """{player_id: {ba, xba, slg, xslg, woba, xwoba, pa}} for the season."""
    def build():
        url = ("https://baseballsavant.mlb.com/leaderboard/expected_statistics"
               f"?type=batter&year={season}&filterType=bip&min=50&csv=true")
        out = {}
        for r in _get_csv(url):
            pid = r.get("player_id")
            if not pid:
                continue
            out[str(pid)] = {
                "ba": _f(r.get("ba")), "xba": _f(r.get("est_ba")),
                "slg": _f(r.get("slg")), "xslg": _f(r.get("est_slg")),
                "woba": _f(r.get("woba")), "xwoba": _f(r.get("est_woba")),
                "pa": _f(r.get("pa")),
            }
        return out
    return _cached(("xstats", season), 6 * 3600, build) or {}


def team_defense(season):
    """{team_id: outs_above_average} for the season -- team fielding OAA from
    Statcast. Positive = the defense turns more balls in play into outs than
    average (fewer hits/runs allowed); negative = a leaky glove."""
    def build():
        url = ("https://baseballsavant.mlb.com/leaderboard/outs_above_average"
               f"?type=Fielding_Team&startYear={season}&endYear={season}&split=no"
               "&team=&range=year&min=q&pos=&roles=&viz=hide&csv=true")
        out = {}
        for r in _get_csv(url):
            tid = r.get("team_id")
            oaa = _f(r.get("outs_above_average"))
            if tid and oaa is not None:
                out[str(tid)] = oaa
        return out
    return _cached(("team_def", season), 6 * 3600, build) or {}


def sprint_speed(season):
    """{player_id: sprint_speed_ft_per_sec} for the season."""
    def build():
        url = ("https://baseballsavant.mlb.com/leaderboard/sprint_speed"
               f"?year={season}&min=5&csv=true")
        out = {}
        for r in _get_csv(url):
            pid = r.get("player_id")
            ss = _f(r.get("sprint_speed"))
            if pid and ss:
                out[str(pid)] = ss
        return out
    return _cached(("speed", season), 6 * 3600, build) or {}


def pitcher_expected_stats(season):
    """{player_id: {"xera": f, "era": f, "xwoba": f, "pa": f}} — the PITCHER
    side of the expected-stats leaderboard, which this app fetched for batters
    only. xERA is Statcast's contact-quality ERA: it sees the exit velo and
    launch angles a pitcher actually allows, which FIP is structurally blind to
    (FIP treats every ball in play as league-average) and ERA sees only through
    sequencing and defense luck. It's the missing fourth read on a starter."""
    def build():
        url = ("https://baseballsavant.mlb.com/leaderboard/expected_statistics"
               f"?type=pitcher&year={season}&filterType=bip&min=50&csv=true")
        out = {}
        for r in _get_csv(url):
            pid = r.get("player_id")
            if not pid:
                continue
            out[str(pid)] = {"xera": _f(r.get("xera")), "era": _f(r.get("era")),
                             "xwoba": _f(r.get("est_woba")), "pa": _f(r.get("pa"))}
        return out
    return _cached(("pitcher_xstats", season), 6 * 3600, build) or {}


def velo_baselines(season):
    """{pitcher_id(str): {"FF": mph, "SI": mph, "FC": mph}} — every pitcher's
    SEASON-average velocity by fastball type, one leaderboard fetch. The
    baseline half of the velocity-drop fatigue flag."""
    def build():
        rows = _get_csv("https://baseballsavant.mlb.com/leaderboard/pitch-arsenals"
                        f"?year={season}&min=100&type=avg_speed&hand=&csv=true", timeout=40)
        out = {}
        for r in rows:
            pid = r.get("pitcher") or r.get("player_id")
            if not pid:
                continue
            d = {}
            for pt, col in (("FF", "ff_avg_speed"), ("SI", "si_avg_speed"),
                            ("FC", "fc_avg_speed")):
                v = _f(r.get(col))
                if v:
                    d[pt] = v
            if d:
                out[str(pid)] = d
        return out
    return _cached(("velo_base", season), 12 * 3600, build) or {}


def last_start_velo(pid, since_date):
    """The pitcher's fastball velocity in his MOST RECENT outing since
    `since_date` -- {"type", "velo", "n", "date"} or None. One small
    statcast_search fetch (a start is ~100 pitches). Velocity is the signal
    that moves BEFORE results do: a starter down 1.5 mph is tired or hurt,
    and his ERA hasn't heard yet."""
    def build():
        url = ("https://baseballsavant.mlb.com/statcast_search/csv?all=true"
               f"&player_type=pitcher&pitchers_lookup%5B%5D={pid}"
               f"&game_date_gt={since_date}&type=details&min_results=0")
        rows = _get_csv(url, timeout=60,
                        keep=("game_date", "pitch_type", "release_speed"))
        if not rows:
            return None
        last_day = max((r.get("game_date") or "") for r in rows)
        if not last_day:
            return None
        by_type = {}
        for r in rows:
            if r.get("game_date") != last_day:
                continue
            pt, v = r.get("pitch_type"), _f(r.get("release_speed"))
            if pt in ("FF", "SI", "FC") and v:
                by_type.setdefault(pt, []).append(v)
        if not by_type:
            return None
        # His primary fastball that day = the type he threw most.
        pt = max(by_type, key=lambda t: len(by_type[t]))
        vs = by_type[pt]
        return {"type": pt, "velo": round(sum(vs) / len(vs), 1),
                "n": len(vs), "date": last_day}
    return _cached(("last_velo", str(pid), since_date), 6 * 3600, build)


def batter_x_splits(pid, season):
    """{"L": {"xwoba": f, "pa": n}, "R": {...}} — the batter's xwOBA split by
    the pitcher's hand, from his own season of Statcast plate appearances
    (one small fetch; the rows carry p_throws). Raw platoon splits are
    notoriously noisy; the x-version judges each PA by contact quality
    instead of outcome, so it stabilizes roughly twice as fast. Per-PA value:
    estimated wOBA on contact, actual wOBA value on K/BB/HBP (the standard
    xwOBA construction)."""
    def build():
        url = ("https://baseballsavant.mlb.com/statcast_search/csv?all=true"
               f"&hfSea={season}%7C&player_type=batter"
               f"&batters_lookup%5B%5D={pid}&type=details&csv=true")
        rows = _get_csv(url, timeout=60,
                        keep=("events", "p_throws", "woba_value",
                              "estimated_woba_using_speedangle"))
        acc = {"L": [0.0, 0], "R": [0.0, 0]}
        for r in rows:
            if not (r.get("events") or "").strip():
                continue                       # not the PA-ending pitch
            hand = r.get("p_throws")
            if hand not in acc:
                continue
            est = _f(r.get("estimated_woba_using_speedangle"))
            val = est if est is not None else _f(r.get("woba_value"))
            if val is None:
                continue
            acc[hand][0] += val
            acc[hand][1] += 1
        out = {}
        for hand, (s, n) in acc.items():
            if n >= 20:
                out[hand] = {"xwoba": round(s / n, 4), "pa": n}
        return out or None
    return _cached(("xsplit", str(pid), season), 6 * 3600, build)


def quality_mults(xs):
    """Contact + power multipliers (dampened, clamped) that nudge a hitter's
    rates toward his expected stats -- crediting hard-hit bad luck and fading
    lucky results. (1.0, 1.0) = no adjustment / no data."""
    if not xs:
        return 1.0, 1.0
    ba, xba = xs.get("ba"), xs.get("xba")
    slg, xslg = xs.get("slg"), xs.get("xslg")
    contact = power = 1.0
    if ba and xba and ba > 0:
        contact = max(0.88, min(1.12, 1 + 0.5 * ((xba - ba) / ba)))
    if slg and xslg and slg > 0:
        power = max(0.85, min(1.15, 1 + 0.5 * ((xslg - slg) / slg)))
    return contact, power


def pitch_arsenals(season):
    """Arsenal matchup data from the Statcast pitch-arsenal leaderboards:

      {"pit": {pid: {pitch_type: usage_fraction}},
       "bat": {pid: {pitch_type: {"k": whiff_mult, "w": woba_mult}}}}

    Batter multipliers are RELATIVE TO HIS OWN overall line (usage-weighted
    across every pitch he saw), shrunk by pitches seen — so vs a league-average
    mix the expected multiplier is ~1 and the season stays calibrated, while a
    breaking-ball-blind hitter facing a slider-heavy starter finally reads as
    one. Everything degrades to {} (no adjustment) on failure."""
    def build():
        base = ("https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats"
                "?type={t}&pitchType=&year={y}&team=&min=5&csv=true")
        pit_rows = _get_csv(base.format(t="pitcher", y=season))
        bat_rows = _get_csv(base.format(t="batter", y=season))

        pit = {}
        for r in pit_rows:
            pid, pt = r.get("player_id"), r.get("pitch_type")
            u = _f(r.get("pitch_usage"))
            if pid and pt and u:
                pit.setdefault(pid, {})[pt] = u / 100.0

        # Batter: per-pitch whiff% and wOBA vs his own usage-weighted overall.
        raw = {}
        for r in bat_rows:
            pid, pt = r.get("player_id"), r.get("pitch_type")
            n = _f(r.get("pitches"))
            wh, wo = _f(r.get("whiff_percent")), _f(r.get("woba"))
            if pid and pt and n and wh is not None and wo is not None:
                raw.setdefault(pid, []).append((pt, n, wh, wo))
        bat = {}
        _KN, _WN = 250.0, 350.0            # shrinkage in pitches seen (whiff / woba)
        for pid, rows in raw.items():
            tot = sum(n for _, n, _, _ in rows) or 1.0
            o_wh = sum(n * wh for _, n, wh, _ in rows) / tot
            o_wo = sum(n * wo for _, n, _, wo in rows) / tot
            if o_wh <= 0 or o_wo <= 0:
                continue
            m = {}
            for pt, n, wh, wo in rows:
                km = (n * (wh / o_wh) + _KN) / (n + _KN)
                wm = (n * (wo / o_wo) + _WN) / (n + _WN)
                m[pt] = {"k": round(max(0.80, min(1.25, km)), 3),
                         "w": round(max(0.85, min(1.18, wm)), 3)}
            bat[pid] = m
        return {"pit": pit, "bat": bat}
    return _cached(("arsenal", season), 12 * 3600, build) or {"pit": {}, "bat": {}}

def handed_hr_factors(year=None):
    """{club_display_name: {"L": residual, "R": residual}} -- how much MORE a
    park rewards home runs from one batter side than its own average.

    Statcast publishes park HR factors split by batter side (the Yankee Stadium
    short porch is a lefty effect, not a park-wide one). The park's OVERALL
    level is already inside the model's run-environment factor, so shipping the
    raw index would double-count it; each side's index is divided by the park's
    two-side mean, leaving only the handedness RESIDUAL (Yankee LHB ~1.1, RHB
    ~0.9). Best-effort: any failure returns {} and nothing downstream changes.
    """
    import clock
    year = year or (clock.today_et().year - 1)     # last FULL season's factors

    def build():
        import json as _json
        import re as _re
        import urllib.request as _rq
        out = {}
        sides = {}
        for side in ("L", "R"):
            url = ("https://baseballsavant.mlb.com/leaderboard/statcast-park-factors"
                   f"?type=year&year={year}&batSide={side}&stat=index_hr"
                   "&condition=All&rolling=")
            req = _rq.Request(url, headers={"User-Agent": _UA})
            body = _rq.urlopen(req, timeout=25).read().decode()
            m = _re.search(r"var data = (\[.*?\]);", body, _re.S)
            if not m:
                return {}
            for r in _json.loads(m.group(1)):
                club = r.get("name_display_club")
                idx = _f(r.get("index_hr"))
                if club and idx:
                    sides.setdefault(club, {})[side] = idx
        for club, d in sides.items():
            if "L" in d and "R" in d:
                mean = (d["L"] + d["R"]) / 2.0
                if mean > 0:
                    out[club] = {"L": round(d["L"] / mean, 3),
                                 "R": round(d["R"] / mean, 3)}
        return out
    return _cached(("park_hand_hr", year), 24 * 3600, build) or {}


def catcher_framing():
    """{team_id: framing runs per 1000 pitches} for the current season, each
    club's catchers pitch-weighted (min 500 pitches a man). 2026 spread runs
    -2.0 to +1.8 rv/1000 across 60 qualified catchers. Cached a day; {} when
    Savant is unreachable, and a missing team is simply neutral."""
    import csv as _csv
    import io as _io
    import urllib.request as _rq

    def build():
        import clock as _ck
        _t = _ck.today_et()
        year = _t.year if _t.month >= 4 else _t.year - 1
        req = _rq.Request(
            "https://baseballsavant.mlb.com/leaderboard/catcher-framing"
            f"?game_type=Regular&year={year}&min=1&csv=true",
            headers={"User-Agent": "Mozilla/5.0"})
        rows = list(_csv.DictReader(_io.StringIO(
            _rq.urlopen(req, timeout=25).read().decode("utf-8-sig"))))
        cs = [(int(r["id"]), float(r["pitches"] or 0), float(r["rv_tot"] or 0))
              for r in rows if float(r["pitches"] or 0) >= 500]
        if not cs:
            return None
        import baseball as _bb
        ids = ",".join(str(i) for i, _, _ in cs)
        d = _bb._get(f"{_bb.STATS_BASE}/people?personIds={ids}"
                     "&hydrate=currentTeam&fields=people,id,currentTeam,id")
        team_of = {p["id"]: (p.get("currentTeam") or {}).get("id")
                   for p in d.get("people", [])}
        agg = {}
        for pid, p, rv in cs:
            t = team_of.get(pid)
            if t:
                a = agg.setdefault(t, [0.0, 0.0])
                a[0] += rv
                a[1] += p
        return {t: round(v[0] / v[1] * 1000, 3) for t, v in agg.items()
                if v[1] > 1000}
    return _cached(("framing",), 24 * 3600, build) or {}
