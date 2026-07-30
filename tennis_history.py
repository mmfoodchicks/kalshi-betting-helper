"""Deep tennis match history -- Jeff Sackmann's results archives.

WHY: tennis_elo rates players from settled Kalshi markets, which only reach back
to when Kalshi started listing tennis. That is a couple of months: the median
player has ~4 matches and barely 1.5% reach 20, so almost every rating on the
board is provisional. Since the Elo is the ONLY model for the ITF matches that
make up most of the board, that shallowness is the single biggest limit on the
tennis numbers.

Sackmann publishes the fix as plain CSV -- every tour match for decades, plus
Challengers, qualifying and ITF futures, each row carrying a date, both players
and the SURFACE. Feeding that in ahead of the Kalshi results gives ratings a real
baseline to be provisional *against*.

TRUST MODEL. This module was written without being able to fetch the files (the
authoring sandbox scopes GitHub per repository owner). So it does not assume the
layout -- it reads the CSV header and resolves each field it needs by name from a
set of accepted spellings, and REJECTS any file whose header does not carry them.
A file that fails validation is skipped and counted, never guessed at. If every
file fails, `results()` returns nothing and tennis_elo carries on with the Kalshi
store exactly as it does today. The failure mode is "no improvement", not "wrong
ratings".

Before relying on it, run:  python3 tests/tennis_history_check.py --live
which fetches the real headers, reports exactly what matched, and re-fits the Elo
K factor over the deepened pool.
"""

import csv
import io
import os
import urllib.request

# Primary is the same host tennis_data already uses for the charting archive.
# The alternates are CDN mirrors of the same repositories, tried in order, so a
# single host being unreachable does not silently disable the whole source.
_HOSTS = (
    "https://raw.githubusercontent.com/JeffSackmann/{repo}/master/{name}",
    "https://cdn.jsdelivr.net/gh/JeffSackmann/{repo}@master/{name}",
    "https://raw.githack.com/JeffSackmann/{repo}/master/{name}",
)

# (tour code, repo, filename template). `kind` is only used for reporting.
_SOURCES = (
    ("m", "tennis_atp", "atp_matches_{year}.csv", "tour"),
    ("m", "tennis_atp", "atp_matches_qual_chall_{year}.csv", "chall/qual"),
    ("m", "tennis_atp", "atp_matches_futures_{year}.csv", "futures"),
    ("w", "tennis_wta", "wta_matches_{year}.csv", "tour"),
    ("w", "tennis_wta", "wta_matches_qual_itf_{year}.csv", "qual/itf"),
)

# Accepted spellings per field. The first that appears in the header wins. Kept
# deliberately broad because the exact header is not verifiable from here -- but
# every candidate must still be PRESENT, so a mismatch fails loudly.
_FIELDS = {
    "date":    ("tourney_date", "tourney_dt", "date", "match_date"),
    "winner":  ("winner_name", "winner", "w_name"),
    "loser":   ("loser_name", "loser", "l_name"),
    "surface": ("surface", "court_surface", "Surface"),
    "level":   ("tourney_level", "level", "tourney_lvl"),
}
_REQUIRED = ("date", "winner", "loser")     # surface/level are optional extras

# Default window. Ratings care about who a player is NOW, and every extra year is
# five more files to fetch; a decade is plenty to take a player off provisional
# without pulling the whole archive on every cold start.
DEFAULT_YEARS = 10
_TIMEOUT = 12
_CACHE_KEY = "tennis_history_rows"
_CACHE_TTL_DAYS = 7
# This runs on the path that builds the Elo pools, so an unreachable archive must
# cost seconds, not the 50 files x 3 hosts x timeout it would otherwise. Two
# guards: a whole-run wall clock, and an early bail if nothing at all parses in
# the first few files -- if the source is gone, it is gone for all of them.
_BUDGET_S = 180
_PROBE_FILES = 3


def enabled():
    """Deep history is on unless explicitly disabled, so a bad rollout can be
    switched off without a deploy."""
    return (os.environ.get("VIGIL_TENNIS_HISTORY", "1") or "1").lower() not in (
        "0", "false", "no", "off")


def _get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "vigil/1.0", "Accept": "text/csv,text/plain,*/*"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def _fetch(repo, name):
    """Text of one archive file, trying each host in turn. None if unreachable."""
    for host in _HOSTS:
        try:
            txt = _get(host.format(repo=repo, name=name))
        except Exception:
            continue
        # A CDN can answer 200 with an error page; a results CSV always has a
        # header line with commas in it.
        if txt and "," in txt.split("\n", 1)[0]:
            return txt
    return None


def _resolve(header):
    """{field: column name} for the fields we need, or None if the header is not
    a recognisable results file."""
    have = {h.strip(): h for h in header}
    out = {}
    for field, names in _FIELDS.items():
        for n in names:
            if n in have:
                out[field] = have[n]
                break
    if any(f not in out for f in _REQUIRED):
        return None
    return out


def _norm_surface(raw):
    """Sackmann writes Hard/Clay/Grass/Carpet; we bucket Carpet with Hard the way
    tennis_data does, and treat anything unrecognised as unknown rather than
    guessing."""
    s = (raw or "").strip().lower()
    if not s:
        return None
    if "clay" in s:
        return "Clay"
    if "grass" in s:
        return "Grass"
    if "hard" in s or "carpet" in s:
        return "Hard"
    return None


def _parse_date(raw):
    """'20240115' -> '20240115'. Also accepts '2024-01-15'. None if unusable."""
    s = (raw or "").strip().replace("-", "").replace("/", "")
    if len(s) == 8 and s.isdigit() and "1900" <= s[:4] <= "2100":
        return s
    return None


def parse(text):
    """One archive CSV -> ([row, ...], reason_rejected). A row is
    (date, winner, loser, surface, level). Rejects rather than guesses."""
    try:
        rdr = csv.reader(io.StringIO(text))
        header = next(rdr, None)
    except Exception as e:
        return [], f"unreadable: {type(e).__name__}"
    if not header:
        return [], "empty file"
    cols = _resolve(header)
    if not cols:
        missing = [f for f in _REQUIRED
                   if not any(n in [h.strip() for h in header] for n in _FIELDS[f])]
        return [], f"header missing {missing} (saw {header[:8]})"
    idx = {f: header.index(c) for f, c in cols.items()}
    n = len(header)
    rows = []
    for rec in rdr:
        if len(rec) < n:
            continue
        d = _parse_date(rec[idx["date"]])
        w = (rec[idx["winner"]] or "").strip()
        l = (rec[idx["loser"]] or "").strip()
        if not d or not w or not l or w == l:
            continue
        rows.append((d, w, l,
                     _norm_surface(rec[idx["surface"]]) if "surface" in idx else None,
                     (rec[idx["level"]] or "").strip() if "level" in idx else ""))
    return rows, None


def fetch(years=DEFAULT_YEARS, upto=None, report=None, budget=_BUDGET_S):
    """[(date, tour, winner, loser, surface, level)] across the archives, in date
    order. `report` (a list) collects one dict per file for diagnostics.

    Bounded on purpose: gives up after `budget` seconds, and abandons the whole
    source if the first few files yield nothing. Callers treat a short read the
    same as no read, so stopping early costs depth, never correctness."""
    import time
    import clock
    end = upto or clock.today_et().year
    started = time.time()
    out, tried, got_any = [], 0, False
    for tour, repo, tmpl, kind in _SOURCES:
        for year in range(end - years + 1, end + 1):
            if time.time() - started > budget:
                if report is not None:
                    report.append({"file": "(budget)", "tour": "", "kind": "",
                                   "ok": False, "rows": 0,
                                   "why": f"stopped after {budget}s"})
                out.sort(key=lambda r: r[0])
                return out
            if tried >= _PROBE_FILES and not got_any:
                if report is not None:
                    report.append({"file": "(probe)", "tour": "", "kind": "",
                                   "ok": False, "rows": 0,
                                   "why": f"nothing parsed in {tried} files, "
                                          "treating the archive as unavailable"})
                return []
            name = tmpl.format(year=year)
            tried += 1
            txt = _fetch(repo, name)
            if txt is None:
                if report is not None:
                    report.append({"file": name, "tour": tour, "kind": kind,
                                   "ok": False, "why": "unreachable", "rows": 0})
                continue
            rows, why = parse(txt)
            if rows:
                got_any = True
            if report is not None:
                report.append({"file": name, "tour": tour, "kind": kind,
                               "ok": bool(rows), "why": why, "rows": len(rows)})
            for d, w, l, s, lv in rows:
                out.append((d, tour, w, l, s, lv))
    out.sort(key=lambda r: r[0])
    return out


def results(years=DEFAULT_YEARS, force=False):
    """Cached deep history. Empty list when disabled or unavailable -- callers
    must treat that as "no extra history", never as an error."""
    if not enabled():
        return []
    try:
        import deep_cache
    except Exception:
        return fetch(years)
    if not force:
        cached, ts = deep_cache.load(_CACHE_KEY)
        if cached:
            try:
                import time
                if (time.time() - (ts or 0)) < _CACHE_TTL_DAYS * 86400:
                    return [tuple(r) for r in cached]
            except Exception:
                return [tuple(r) for r in cached]
    rows = fetch(years)
    if rows:
        try:
            deep_cache.save(_CACHE_KEY, rows)
        except Exception:
            pass
    return rows
