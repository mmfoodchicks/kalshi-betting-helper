"""Deep tennis match history -- ATP tour results, 20 years deep.

WHY: tennis_elo rates players from settled Kalshi markets, which only reach back
to when Kalshi started listing tennis. That is a couple of months: the median
player has ~4 matches and barely 1.5% reach 20, so almost every rating on the
board is provisional. Since the Elo is the ONLY model for the ITF matches that
make up most of the board, that shallowness is the single biggest limit on the
tennis numbers.

The fix is plain CSV -- one file per year, each row carrying a date, both players
and the SURFACE. Fed in ahead of the Kalshi results, it gives ratings a real
baseline to be provisional *against*. VERIFIED against the live source: 436,751
matches parse cleanly from 48 of 48 files in ~40s, taking players with 20+ rated
matches from 1.5% to 33% and the pools from 8,341 to 19,409 players.

Crucially it now reaches the ITF tier -- futures for men, ITF/qualifying for
women -- which is where most of a Kalshi tennis board sits and which previously
had no historical source at all. ITF sides on the board carry a median 72 rated
matches, and matches shown as "market, no model" fell from 39 to 5.

TRUST MODEL. The parser does not assume a layout -- it reads the CSV header and
resolves each field it needs by name from a set of accepted spellings, in any
column order, and REJECTS any file whose header does not carry a date, a winner
and a loser. A file that fails validation is skipped and counted, never guessed
at. If every file fails, `results()` returns nothing and tennis_elo carries on
with the Kalshi store exactly as before: the failure mode is "no improvement",
not "wrong ratings". VIGIL_TENNIS_HISTORY=0 disables it without a deploy.

Check it with:  python3 tests/tennis_history_check.py --live
"""

import csv
import io
import os
import urllib.request

_HF = "https://huggingface.co/datasets/Aneeshers/tennis-sackmann-archive/resolve/main"
_TML = "https://raw.githubusercontent.com/Tennismylife/TML-Database/master"

# (tour code, URL template taking {year}, kind).
#
# The canonical archives -- JeffSackmann/tennis_atp and tennis_wta -- are gone
# from GitHub. Every path in them returns GitHub's own 404 including README.md,
# while his charting project serves fine from the same (demonstrably ungated)
# host, and his profile now lists only that. They cannot be fetched or forked.
#
# Aneeshers/tennis-sackmann-archive on HuggingFace is a full mirror of them: 473
# files, byte-identical schema, updated within the last couple of months. Crucially
# it carries the parts that matter most here and that no other reachable source
# has -- ITF FUTURES for men (atp_matches_futures_*) and ITF for women
# (wta_matches_qual_itf_*), which is the tier most of a Kalshi tennis board sits
# in and which had no historical source at all.
#
# TML-Database stays for ATP tour, but NOT for recency -- measured, its current
# year holds 137 rows against the mirror's 1,449, and its settled years duplicate
# the mirror almost row for row (3076/3076, 2943/2944). It earns its place as the
# one source on a DIFFERENT host: the canonical GitHub repos vanished without
# warning, and if HuggingFace goes the same way this still returns ATP tour
# instead of nothing. The overlap is free -- _build dedups on (date, players).
_SOURCES = (
    ("m", _HF + "/atp/atp_matches_futures_{year}.csv", "ITF men"),
    ("w", _HF + "/wta/wta_matches_qual_itf_{year}.csv", "ITF women"),
    ("m", _HF + "/atp/atp_matches_qual_chall_{year}.csv", "chall/qual"),
    ("m", _HF + "/atp/atp_matches_{year}.csv", "atp tour"),
    ("w", _HF + "/wta/wta_matches_{year}.csv", "wta tour"),
    ("m", _TML + "/{year}.csv", "atp tour (live)"),
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

# Window. Ratings care about who a player is NOW, and the archive is much bigger
# than it was when this pulled one small file per year -- ITF alone runs ~18k
# men's and ~22k women's matches annually. Eight years takes every player on a
# board well clear of provisional (the K ramp settles by ~50 matches) without
# pulling a decade of juniors who have since retired. Cached for a week, so the
# fetch cost is paid rarely -- but the MEMORY cost is paid on every rebuild, which
# is why the depth is sized to the host below.
def default_years():
    """How deep to pull, sized to the box rather than fixed.

    Eight years is 436k matches, and turning those into Elo pools peaks near
    440 MB -- more than a 512 MB container has, so the rebuild is what kills the
    instance rather than merely being slow. Memory scales with the row count, so
    a small host takes fewer years: shallower ratings still beat no board at all,
    and the pools persist across restarts so this rebuild is rare either way.
    VIGIL_TENNIS_YEARS overrides."""
    env = os.environ.get("VIGIL_TENNIS_YEARS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    for path in ("/sys/fs/cgroup/memory.max",
                 "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(path) as f:
                raw = f.read().strip()
            if raw in ("max", "-1"):
                continue
            mb = int(raw) / (1024 * 1024)
            if 0 < mb < (1 << 22):
                return 3 if mb < 768 else (5 if mb < 1536 else 8)
        except Exception:
            continue
    return 8


DEFAULT_YEARS = 8               # the full-fat depth, used when memory allows
_TIMEOUT = 12
# The cache key carries the DEPTH. Without it a box that once cached eight years
# would keep loading all 436k rows even after dropping to three -- the row list is
# ~146 MB in memory, so serving the wrong depth from cache would defeat the whole
# point of shortening it.
_CACHE_PREFIX = "tennis_history_rows"
_CACHE_TTL_DAYS = 7
# This runs on the path that builds the Elo pools, so an unreachable archive must
# cost seconds, not the 50 files x 3 hosts x timeout it would otherwise. Two
# guards: a whole-run wall clock, and an early bail if nothing at all parses in
# the first few files -- if the source is gone, it is gone for all of them.
_BUDGET_S = 300
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


def _fetch(url):
    """Text of one archive file, or None. A host can answer 200 with an error
    page, so require a header line that looks like CSV."""
    try:
        txt = _get(url)
    except Exception:
        return None
    return txt if (txt and "," in txt.split("\n", 1)[0]) else None


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


def fetch(years=None, upto=None, report=None, budget=_BUDGET_S):
    """[(date, tour, winner, loser, surface, level)] across the archives, in date
    order. `report` (a list) collects one dict per file for diagnostics.

    Bounded on purpose: gives up after `budget` seconds, and abandons the whole
    source if the first few files yield nothing. Callers treat a short read the
    same as no read, so stopping early costs depth, never correctness."""
    import time
    import clock
    years = years or default_years()
    end = upto or clock.today_et().year
    started = time.time()
    out, tried, got_any = [], 0, False
    for tour, tmpl, kind in _SOURCES:
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
            url = tmpl.format(year=year)
            name = url.rsplit("/", 1)[-1]
            tried += 1
            txt = _fetch(url)
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


def results(years=None, force=False):
    """Cached deep history. Empty list when disabled or unavailable -- callers
    must treat that as "no extra history", never as an error."""
    if not enabled():
        return []
    years = years or default_years()
    try:
        import deep_cache
    except Exception:
        return fetch(years)
    if not force:
        cached, ts = deep_cache.load(f"{_CACHE_PREFIX}_{years}y")
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
            deep_cache.save(f"{_CACHE_PREFIX}_{years}y", rows)
        except Exception:
            pass
    return rows
