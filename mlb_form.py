"""Recent form for MLB hitters and pitchers -- the last-10-game trend layer.

The season line is what a player has BEEN. It is not always what he is. A hitter
carrying a .240 season mark who is 14-for-38 over two weeks is a different bet
than the .240 suggests, and the sim had no way to know: batter rates came from
season OPS regressed toward the team, with no recency term at all. (Starters
already had one -- baseball._starter_ra9 blends a lastX split at RECENT_WEIGHT --
so this closes the gap on the hitting side.)

CHEAP ON PURPOSE. MLB's statsapi will serve the last-10-game line for every
qualified player in the league in ONE request:

    /stats?stats=lastXGames&group=hitting&sportId=1&limit=1500&playerPool=All
    -> 519 hitters, ~2s

which is what makes this affordable on a memory-constrained box (measured on
the 512 MB plan). Per-player game logs would
be ~350 requests for one slate. Note that `limit` paginates PLAYERS, not games:
the window is fixed at 10 by the endpoint, so LAST_X below documents what the
feed returns rather than requesting it.

HOW MUCH IT MOVES. Not much, and that is deliberate. Ten games is ~40 plate
appearances, which is noise-dominated -- a .400 stretch over 40 PA is a coin that
came up heads, most of the time. So form is applied as a REGRESSED multiplier on
the season rate, weighted by the actual PA behind it (_FORM_PA), capped hard, and
it moves a hitter a few percent rather than replacing his talent estimate. The
honest use of a hot streak is a nudge and a label, not a new projection.

VIGIL_MLB_FORM=0 disables the whole layer; every consumer treats an empty result
as "no form data" and falls back to the season number.
"""

import os
import errlog

STATS_BASE = "https://statsapi.mlb.com/api/v1"
LAST_X = 10                  # what the lastXGames feed returns
_TTL = 3600                  # form changes once a day; an hour is plenty

# Plate appearances at which a form read gets HALF its weight. ~40 PA is one
# 10-game window, so a full window earns about half the pull toward its own rate
# and the cap below bounds the rest.
_FORM_PA = 40.0
_MAX_FORM = 0.12             # a form multiplier is clamped to +/- this
_IP_HALF = 12.0              # innings for half weight on a pitcher's form


def enabled():
    return (os.environ.get("VIGIL_MLB_FORM", "1") or "1").lower() not in (
        "0", "false", "no", "off")


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _fetch(group, season):
    import json
    import urllib.request
    url = (f"{STATS_BASE}/stats?stats=lastXGames&group={group}&season={season}"
           f"&sportId=1&limit=1500&playerPool=All")
    req = urllib.request.Request(url, headers={"User-Agent": "vigil/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def _hitters(season):
    """{player_id: {...rate stats over the last 10 games...}}."""
    out = {}
    try:
        data = _fetch("hitting", season)
    except Exception:
        return out
    for s in (data.get("stats") or [{}])[0].get("splits", []):
        pid = ((s.get("player") or {}).get("id"))
        st = s.get("stat") or {}
        if not pid:
            continue
        ab = _f(st.get("atBats"))
        bb = _f(st.get("baseOnBalls"))
        hbp = _f(st.get("hitByPitch"))
        sf = _f(st.get("sacFlies"))
        pa = ab + bb + hbp + sf
        if pa <= 0:
            continue
        h = _f(st.get("hits"))
        tb = (h + _f(st.get("doubles")) + 2 * _f(st.get("triples"))
              + 3 * _f(st.get("homeRuns")))
        out[pid] = {"pa": pa, "ab": ab, "g": _f(st.get("gamesPlayed")),
                    "h": h, "hr": _f(st.get("homeRuns")),
                    "avg": (h / ab) if ab else 0.0,
                    "ops": _f(st.get("onBasePlusSlugging"),
                              _f(st.get("ops"))) or _slash_ops(st, ab, h, tb, bb, hbp, sf),
                    "tb": tb, "k": _f(st.get("strikeOuts")),
                    "rbi": _f(st.get("rbi")), "r": _f(st.get("runs"))}
    return out


def _slash_ops(st, ab, h, tb, bb, hbp, sf):
    """OPS from the components when the feed does not carry it directly."""
    obp_den = ab + bb + hbp + sf
    obp = (h + bb + hbp) / obp_den if obp_den else 0.0
    slg = tb / ab if ab else 0.0
    return round(obp + slg, 3)


def _pitchers(season):
    """{player_id: {...rate stats over the last 10 appearances...}}."""
    out = {}
    try:
        data = _fetch("pitching", season)
    except Exception:
        return out
    for s in (data.get("stats") or [{}])[0].get("splits", []):
        pid = ((s.get("player") or {}).get("id"))
        st = s.get("stat") or {}
        if not pid:
            continue
        ip = _ip(st.get("inningsPitched"))
        if ip <= 0:
            continue
        er = _f(st.get("earnedRuns"))
        out[pid] = {"ip": ip, "g": _f(st.get("gamesPlayed")),
                    "era": (er * 9.0 / ip) if ip else 0.0,
                    "whip": ((_f(st.get("hits")) + _f(st.get("baseOnBalls"))) / ip)
                            if ip else 0.0,
                    "k9": (_f(st.get("strikeOuts")) * 9.0 / ip) if ip else 0.0}
    return out


def _ip(v):
    """MLB writes innings as 5.1 / 5.2 meaning 5 1/3 / 5 2/3, not decimal."""
    try:
        s = str(v)
        whole, _, frac = s.partition(".")
        return int(whole) + (int(frac[0]) / 3.0 if frac else 0.0)
    except (TypeError, ValueError):
        return 0.0


def form(season, force=False):
    """{"hitting": {pid: {...}}, "pitching": {pid: {...}}}, cached.

    Empty on any failure -- callers must treat that as "no form data" and use the
    season number, never as an error."""
    if not enabled():
        return {"hitting": {}, "pitching": {}}
    key = f"mlb_form_{season}"
    try:
        import deep_cache
    except Exception:
        return {"hitting": _hitters(season), "pitching": _pitchers(season)}
    if not force:
        cached, ts = deep_cache.load(key)
        if cached:
            import time
            if time.time() - (ts or 0) < _TTL:
                return cached
    out = {"hitting": _hitters(season), "pitching": _pitchers(season)}
    if out["hitting"] or out["pitching"]:
        try:
            deep_cache.save(key, out)
        except Exception as _e:
            errlog.note("FORM-form", _e)
    return out


def hitter_factor(rec, season_ops, lg_ops=0.715):
    """Multiplier on a hitter's season rate from his last-10 line, or 1.0.

    Regressed by the plate appearances actually behind it and clamped, so a
    two-week heater nudges the projection instead of replacing it. Returns
    (factor, note) where note is a short human string, or (1.0, None)."""
    if not rec or not season_ops or season_ops <= 0:
        return 1.0, None
    pa = rec.get("pa") or 0
    if pa < 8:
        return 1.0, None
    recent_ops = rec.get("ops") or 0.0
    if recent_ops <= 0:
        return 1.0, None
    w = pa / (pa + _FORM_PA)
    raw = recent_ops / season_ops - 1.0
    adj = max(-_MAX_FORM, min(_MAX_FORM, raw * w))
    ab, h = rec.get("ab") or 0, rec.get("h") or 0
    note = None
    if abs(adj) >= 0.02 and ab >= 15:
        # Lead with OPS, because OPS is what moved the number. Quoting the
        # batting average instead produced notes that contradicted their own tag
        # -- a hitter walking and slugging his way to a .900 stretch OPS reads
        # "hot" while his line says 9-for-37 (.243), which looks like a bug.
        note = (f"{recent_ops:.3f} OPS (season {season_ops:.3f}) over "
                f"{int(rec.get('g') or 0)}G, {int(h)}-for-{int(ab)}")
    return 1.0 + adj, note


def pitcher_factor(rec, season_era, lg_era=4.20):
    """Multiplier on a starter's expected runs allowed from his last-10 line.

    BELOW 1.0 means pitching better than his season number (fewer runs), so
    callers apply it to a runs-allowed rate, not to a quality score."""
    if not rec or not season_era or season_era <= 0:
        return 1.0, None
    ip = rec.get("ip") or 0
    if ip < 4:
        return 1.0, None
    # A 0.00 ERA over a real innings sample is DATA, not a missing value, and
    # rejecting it threw away the strongest form reads on the board. Only a
    # negative (impossible) figure is treated as unusable.
    recent = rec.get("era")
    if recent is None or recent < 0:
        return 1.0, None
    w = ip / (ip + _IP_HALF)
    raw = recent / season_era - 1.0
    adj = max(-_MAX_FORM, min(_MAX_FORM, raw * w))
    note = None
    if abs(adj) >= 0.02:
        note = f"{recent:.2f} ERA over his last {int(rec.get('g') or 0)}"
    return 1.0 + adj, note


def trend_tag(rec, season_ops):
    """A short label for the UI: 'hot' / 'cold' / None. Deliberately requires a
    real sample AND a real gap, so most players get no tag at all."""
    f, _note = hitter_factor(rec, season_ops)
    if (rec or {}).get("pa", 0) < 20:
        return None
    if f >= 1.05:
        return "hot"
    if f <= 0.95:
        return "cold"
    return None
