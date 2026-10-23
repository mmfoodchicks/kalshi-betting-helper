"""nflverse play-by-play, participation and FTN charting, compacted per season --
the data under the NFL offense-vs-defense matchup engine.

The app's football stack reaches the play level only through ESPN's summaries
(nfl_pbp.py: fourteen ints a play, no players, no coverage). The matchup
question -- how a receiver's usage and style meet a defense's actual coverage,
personnel and pressure -- needs three public nflverse files joined on
(game_id, play_id):

  pbp/play_by_play_<Y>.csv.gz        every play with its passer / receiver /
                                      rusher gsis ids, air yards, YAC, EPA, CPOE,
                                      xpass, pass location, run gap (2021-2026;
                                      19 MB gzipped, ~50,000 rows a season)
  pbp_participation/pbp_participation_<Y>.csv
                                      NGS charting: offense / defense personnel
                                      strings, defenders in box, pass rushers,
                                      time to throw, was_pressure, the targeted
                                      receiver's route, defense_man_zone_type and
                                      defense_coverage_type (COVER_0/1/2/3/4/6/9,
                                      2_MAN, COMBO, BLOWN -- charted POST-snap),
                                      and the 22 gsis ids on the field. 2021-2025
                                      only: NO 2026 FILE EXISTS (2026-10-23), so
                                      2026 coverage tendencies must be carried
                                      from 2025 (nfl_matchup_profiles).
  ftn_charting/ftn_charting_<Y>.csv   FTN Data via nflverse (CC-BY-SA 4.0):
                                      motion, play action, screen, RPO, blitzers,
                                      pass rushers, box count, catchable /
                                      contested / drop, read thrown, QB out of
                                      pocket, starting hash (2022-2026). No
                                      coverage.

Measured on 2023 (this sandbox, 2026-10-23): pbp 19.2 MB gzipped, participation
50.0 MB, FTN 8.3 MB; 35,600 pass/run plays and every one of them joined to a
participation row; coverage is charted on ~22,900 passes a season. The compact
season store (deep store key nfl_matchup_<season>) keeps one array per column
over the offensive snaps (pass, run, kneel, spike) of the REGULAR season --
roughly 60 columns x 36,000 plays of 1-4 bytes -- plus, per game, each offensive
player's snaps on the field split by the coverage he faced, so "targets per
pass snap vs man" has a denominator without storing 22 ids a play. Everything
is pure stdlib: this module is importable by the web app, which has no numpy.

Nothing here is simulated or estimated: every number is a count or a sum read
off the files, and the store records which file (URL, bytes, rows, and the
server's Last-Modified) each season came from. The refresh is a build, not a
request path: it downloads ~80 MB a season and parses for ~20 s, so it belongs
in a PC-first deep job (see refresh()).

Player identity: nflverse's gsis id ("00-0036355") is the key everywhere, kept
as the integer 36355. The crosswalk to Sleeper (the app's projections) and ESPN
comes from nflverse players.csv (espn_id) and DynastyProcess db_playerids.csv
(sleeper_id, espn_id); names are normalised with kalshi_nfl._norm so a Kalshi
prop title lands on the same key as a Sleeper roster name.
"""
import array
import csv
import gzip
import io
import os
import re
import time
import urllib.request

import deep_cache
import errlog
import kalshi_nfl

# 2: the on-field vector carries the player's TARGETS by coverage beside his
#    dropbacks (so every per-dropback rate has numerator and denominator from
#    the same plays), PREVENT is a coverage code, the 2016-2022 route and
#    formation names are codes of their own, kneels/spikes leave the on-field
#    tallies, FTN's hash and QB location are decoded.
SCHEMA = 2
KEY = "nfl_matchup_{season}"
IDS_KEY = "nfl_matchup_ids"
# Every store and every table derived from one carries this. FTN's charting is
# CC BY-SA 4.0 and must be credited "FTN Data via nflverse"; nflverse's own
# files (pbp, participation, players, snap counts) are CC BY 4.0.
CREDIT = {"ftn": "FTN Data via nflverse (CC BY-SA 4.0)", "nflverse": "nflverse-data (CC BY 4.0)",
          "ids": "nflverse players.csv + DynastyProcess db_playerids.csv"}

NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download/{tag}/{file}"
DP_IDS = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"
# The seasons each source exists for (checked 2026-10-23). Participation ends at
# 2025; FTN starts at 2022.
# Participation exists from 2016 (coverage, routes, pressure and personnel are
# populated at least from 2020, checked 2026-10-23), FTN from 2022. nflreadr:
# participation from 2023 on is published "after all post-season games are
# completed" and does not update in season -- the 2026 file arrives in 2027.
PARTICIPATION_SEASONS = range(2016, 2026)
FTN_SEASONS = range(2022, 2027)
PBP_SEASONS = range(2016, 2027)
SKILL = ("QB", "RB", "WR", "TE", "FB")

# ---- enumerations (stable codes; the store carries no strings per play) --------
# PREVENT is charted 2016-2022 only (70-97 dropbacks a season); BLOWN, COMBO
# and COVER_9 from 2023. Codes are appended, never reordered (the stores hold them).
COVERAGE = ("", "COVER_0", "COVER_1", "COVER_2", "COVER_3", "COVER_4", "COVER_6",
            "COVER_9", "2_MAN", "COMBO", "BLOWN", "PREVENT")
COV_IX = {c: i for i, c in enumerate(COVERAGE)}
# Shell families. Single-high: one deep safety (Cover 1 man-free, Cover 3 zone).
# Two-high: Cover 2, Cover 4 (quarters), Cover 6 (quarter-quarter-half) and
# 2-Man. Zero: no deep safety. COVER_9, COMBO and BLOWN are kept apart as
# "other": Cover 9 is a three-deep match concept whose shell varies by
# charter, COMBO is a mixed call, BLOWN a busted one.
COV_FAMILY = {"COVER_1": "single_high", "COVER_3": "single_high",
              "COVER_2": "two_high", "COVER_4": "two_high", "COVER_6": "two_high", "2_MAN": "two_high",
              "COVER_0": "zero", "COVER_9": "other", "COMBO": "other", "BLOWN": "other", "PREVENT": "other"}
MZ = ("", "MAN_COVERAGE", "ZONE_COVERAGE")
MZ_IX = {c: i for i, c in enumerate(MZ)}
# Two route vocabularies. 2016-2022: HITCH, OUT, FLAT, CROSS, IN, ANGLE, GO,
# SCREEN, SLANT, POST, CORNER, WHEEL. 2023 on: QUICK OUT, DEEP OUT, HITCH/CURL,
# SHALLOW CROSS/DRAG, IN/DIG, TEXAS/ANGLE, SWING and the shared GO, SCREEN,
# SLANT, POST, CORNER, WHEEL. Every name decodes to its own code; the family
# map below is only a label, because the two vocabularies cut the short game
# differently (measured on the raw files: quick 62% -> 50%, screen 9% -> 15%,
# intermediate 10% -> 17% at the 2022/2023 boundary with OUT/CROSS/FLAT mapped
# as below, while vertical holds at 18.1% both sides) -- so the profiles build
# route MIXES from ROUTE_ERA on only (nfl_matchup_profiles).
ROUTES = ("", "QUICK OUT", "HITCH/CURL", "SCREEN", "IN/DIG", "GO", "SHALLOW CROSS/DRAG",
          "DEEP OUT", "SLANT", "POST", "SWING", "CORNER", "WHEEL", "TEXAS/ANGLE", "OTHER",
          "HITCH", "OUT", "FLAT", "CROSS", "IN", "ANGLE")
ROUTE_IX = {c: i for i, c in enumerate(ROUTES)}
ROUTE_ERA = 2023
# Route families for the player and defense splits: what the route asks of the
# coverage, not its name.
ROUTE_FAMILY = {"QUICK OUT": "quick", "HITCH/CURL": "quick", "SLANT": "quick",
                "SHALLOW CROSS/DRAG": "quick", "SCREEN": "screen", "SWING": "screen",
                "IN/DIG": "intermediate", "DEEP OUT": "intermediate", "TEXAS/ANGLE": "intermediate",
                "GO": "vertical", "POST": "vertical", "CORNER": "vertical", "WHEEL": "vertical",
                "OTHER": "other",
                "HITCH": "quick", "OUT": "quick", "FLAT": "quick", "CROSS": "quick",
                "IN": "intermediate", "ANGLE": "intermediate"}
# Formations: 2016-2022 SHOTGUN / SINGLEBACK / I_FORM / EMPTY / PISTOL / JUMBO /
# WILDCAT; 2023 on SHOTGUN / UNDER CENTER / PISTOL. Decoded as written.
FORMATION = ("", "SHOTGUN", "SINGLEBACK", "I_FORM", "EMPTY", "PISTOL", "JUMBO", "WILDCAT", "OTHER", "UNDER CENTER")
FORM_IX = {c: i for i, c in enumerate(FORMATION)}
# FTN's starting hash (L / M / R) and QB location (U under center, S shotgun, P pistol).
HASH = ("", "L", "M", "R")
HASH_IX = {c: i for i, c in enumerate(HASH)}
QBLOC = ("", "U", "S", "P")
QBLOC_IX = {c: i for i, c in enumerate(QBLOC)}
READ = ("", "1", "2", "CHK", "DES", "SD", "0", "OTHER")
READ_IX = {c: i for i, c in enumerate(READ)}
SIDE = ("", "left", "middle", "right")
SIDE_IX = {c: i for i, c in enumerate(SIDE)}
GAP = ("", "end", "tackle", "guard")
GAP_IX = {c: i for i, c in enumerate(GAP)}
# kind: what the snap was
RUN, ATTEMPT, SACK, SCRAMBLE, KNEEL, SPIKE, OTHER_DB = range(7)
KIND = ("run", "attempt", "sack", "scramble", "kneel", "spike", "other_dropback")

NA = -1               # the "no data" sentinel in every signed small column
NA_H = -32768         # ... and in the scaled 16-bit ones
PERS_UNKNOWN = 255    # offence personnel that is not <RB><TE> shaped

# One array per column: (name, array typecode). 'b' signed byte, 'B' unsigned,
# 'h'/'H' 16-bit, 'i' 32-bit. The order is the store's contract (SCHEMA).
COLUMNS = (
    ("gi", "H"), ("week", "B"), ("off", "B"), ("dfn", "B"), ("qtr", "B"), ("down", "B"),
    ("ytg", "B"), ("yl", "B"), ("gsr", "H"), ("sd", "h"), ("wp", "H"),
    ("kind", "B"), ("db", "B"), ("yds", "h"), ("air", "b"), ("yac", "b"), ("epa", "h"),
    ("cpoe", "h"), ("xpass", "h"), ("succ", "b"), ("comp", "B"), ("td", "B"), ("ptd", "B"),
    ("rtd", "B"), ("intc", "B"), ("fuml", "B"), ("fd", "B"), ("sg", "B"), ("nh", "B"),
    ("ploc", "B"), ("plen", "B"), ("rloc", "B"), ("rgap", "B"), ("g2g", "B"), ("qbhit", "B"),
    ("passer", "i"), ("receiver", "i"), ("rusher", "i"), ("fumbler", "i"),
    # participation (NA where the play had no row or the field was blank)
    ("part", "B"), ("ndl", "b"), ("nlb", "b"), ("ndb", "b"), ("box", "b"), ("rushers", "b"),
    ("ttt", "h"), ("press", "b"), ("route", "B"), ("mz", "B"), ("cov", "B"), ("opers", "B"),
    ("nwr", "b"), ("nol", "b"), ("oform", "B"),
    # FTN (NA where the play had no row)
    ("ftn", "B"), ("motion", "b"), ("pa", "b"), ("screen", "b"), ("rpo", "b"), ("blitzers", "b"),
    ("frush", "b"), ("fbox", "b"), ("catchable", "b"), ("contested", "b"), ("drop", "b"),
    ("readt", "B"), ("oop", "b"), ("trick", "b"), ("sneak", "b"), ("intw", "b"),
    ("throwaway", "b"), ("created", "b"), ("qbfault", "b"), ("hash", "b"), ("qbloc", "b"),
    ("nbf", "b"),
)
COL_IX = {c: i for i, (c, _t) in enumerate(COLUMNS)}

# On-field vector per (game, offensive player): snaps, dropbacks, designed runs,
# dropbacks vs man, vs zone, the index (in the store's `teams`) of the offence
# he was on the field for, dropbacks under each COVERAGE code 1..N, then HIS
# TARGETS on those same plays: all, vs man, vs zone, under each code. A target
# is counted here only when the player is on the play's on-field list, so
# every "targets per dropback on the field" rate is <= 1 by construction and
# a season with no participation file (2026) adds to neither side. Kneels and
# spikes are not snaps here (a kneel-only appearance is not a game played).
_NC = len(COVERAGE) - 1
ONFIELD_W = 6 + _NC + 3 + _NC
OF_SNAPS, OF_DB, OF_RUSH, OF_MAN, OF_ZONE, OF_TEAM, OF_COV0 = 0, 1, 2, 3, 4, 5, 6
OF_TGT = OF_COV0 + _NC
OF_TGT_MAN, OF_TGT_ZONE, OF_TCOV0 = OF_TGT + 1, OF_TGT + 2, OF_TGT + 3


# ---- small parsers --------------------------------------------------------------
def gsis_int(s):
    """'00-0036355' -> 36355; anything else -> 0."""
    s = (s or "").strip()
    if len(s) == 10 and s.startswith("00-") and s[3:].isdigit():
        return int(s[3:])
    return 0


def gsis_str(n):
    return "00-%07d" % int(n) if n else ""


def _i(v, default=NA):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bool(v):
    v = (v or "").strip().upper()
    if v in ("TRUE", "1", "T"):
        return 1
    if v in ("FALSE", "0", "F"):
        return 0
    return NA


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


_PERS_RE = re.compile(r"(\d+)\s+([A-Z]+)")


def parse_personnel(s):
    """'3 CB, 2 DE, 2 DT, 1 FS, 1 MLB, 1 OLB, 1 SS' -> {'CB': 3, 'DE': 2, ...}."""
    out = {}
    for n, pos in _PERS_RE.findall((s or "").upper()):
        out[pos] = out.get(pos, 0) + int(n)
    return out


def defense_groups(pers):
    """(DL, LB, DB) from a parsed defense personnel dict. DL = DE + DT + NT + DL;
    LB = ILB + OLB + MLB + LB; DB = CB + FS + SS + S + DB. Offensive position
    names on a defensive string (a punt-team row) count toward nothing."""
    dl = sum(pers.get(k, 0) for k in ("DE", "DT", "NT", "DL"))
    lb = sum(pers.get(k, 0) for k in ("ILB", "OLB", "MLB", "LB"))
    db = sum(pers.get(k, 0) for k in ("CB", "FS", "SS", "S", "DB"))
    return dl, lb, db


def offense_code(pers):
    """(code, n_wr, n_ol): the conventional two-digit personnel code (RB then TE:
    '11' = 1 RB, 1 TE; '12' = 1 RB, 2 TE; '21' = 2 RB, 1 TE) with a fullback
    counted as a back, or PERS_UNKNOWN when either count exceeds three. n_ol
    counts C + G + T (six-plus is a jumbo line) and is readable from 2023 only:
    the 2016-2022 strings list the skill positions alone ('1 RB, 1 TE, 3 WR'),
    so n_ol reads 0 there."""
    rb = pers.get("RB", 0) + pers.get("FB", 0) + pers.get("HB", 0)
    te = pers.get("TE", 0)
    wr = pers.get("WR", 0)
    ol = pers.get("C", 0) + pers.get("G", 0) + pers.get("T", 0) + pers.get("OL", 0)
    code = rb * 10 + te if rb <= 3 and te <= 3 else PERS_UNKNOWN
    return code, wr, ol


def depth_bucket(air):
    """Air yards -> 0 behind the line, 1 short (0-9), 2 intermediate (10-19),
    3 deep (20+); None when air yards are unknown."""
    if air is None or air == NA or air <= -100:
        return None
    if air < 0:
        return 0
    if air < 10:
        return 1
    if air < 20:
        return 2
    return 3


DEPTH = ("behind", "short", "intermediate", "deep")


def dd_bucket(down, ytg):
    """Down-and-distance bucket: 'early' (1st, 2nd & 1-6), '2nd_long' (2nd & 7+),
    '3rd_short' (3rd/4th & 1-3), '3rd_med' (4-6), '3rd_long' (7+)."""
    if down <= 1 or (down == 2 and ytg <= 6):
        return "early"
    if down == 2:
        return "2nd_long"
    if ytg <= 3:
        return "3rd_short"
    if ytg <= 6:
        return "3rd_med"
    return "3rd_long"


def norm_name(name):
    """The app's player key: kalshi_nfl._norm (accents, suffixes, punctuation
    gone), so Kalshi's titles, Sleeper's rosters and nflverse's names agree."""
    return kalshi_nfl._norm(name)


# ---- fetching -------------------------------------------------------------------
def url_for(kind, season=None):
    if kind == "pbp":
        return NFLVERSE.format(tag="pbp", file=f"play_by_play_{season}.csv.gz")
    if kind == "participation":
        return NFLVERSE.format(tag="pbp_participation", file=f"pbp_participation_{season}.csv")
    if kind == "ftn":
        return NFLVERSE.format(tag="ftn_charting", file=f"ftn_charting_{season}.csv")
    if kind == "players":
        return NFLVERSE.format(tag="players", file="players.csv")
    if kind == "snaps":
        return NFLVERSE.format(tag="snap_counts", file=f"snap_counts_{season}.csv")
    if kind == "dp_ids":
        return DP_IDS
    raise ValueError(kind)


def remote_modified(url, timeout=20):
    """Upstream's Last-Modified for `url` (one HEAD request), or None when it
    cannot be asked -- ledgered, so a refresh that keeps a stale file is
    visible rather than silent."""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "vigil-nfl-matchup/1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.headers.get("Last-Modified")
    except Exception as e:
        errlog.note("NFLM-head", e, path=url)
        return None


def _meta_path(path):
    return path + ".meta.json"


def _write_meta(path, meta):
    """The record of what a downloaded file IS: {url, modified, bytes,
    fetched}. _fresh compares its `modified` with upstream's."""
    import json
    tmp = _meta_path(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    os.replace(tmp, _meta_path(path))


def _read_meta(path):
    import json
    try:
        with open(_meta_path(path), encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def _fresh(path, url, max_age_s=None):
    """Is the download at `path` still what upstream serves? A file younger
    than `max_age_s` is taken as fresh without asking; otherwise upstream's
    Last-Modified must equal the one recorded when the file was fetched. A
    file with no record is stale (it will be fetched once and recorded); when
    upstream cannot be asked the file on disk is kept (never a broken build,
    and remote_modified ledgered why). The first version returned True for
    any existing file, so a daily job would have rebuilt the same download
    forever (research/reviews/nfl_matchup_data.md F5)."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    if max_age_s is not None and time.time() - os.path.getmtime(path) < max_age_s:
        return True
    have = _read_meta(path).get("modified")
    if not have:
        return False
    remote = remote_modified(url)
    if remote is None:
        return True
    return remote == have


def fetch(url, dest, timeout=120, retries=2, log=None):
    """Stream one file to `dest` (atomic rename) and record what it is beside
    it (_write_meta). Returns {url, bytes, modified, seconds}. Raises after
    `retries` failures -- a partial file is never left behind, and a missing
    season is the caller's decision, not a silent gap."""
    last = None
    for attempt in range(retries + 1):
        t0 = time.time()
        tmp = dest + ".part"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "vigil-nfl-matchup/1"})
            with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as fh:
                modified = resp.headers.get("Last-Modified")
                n = 0
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
                    n += len(chunk)
            os.replace(tmp, dest)
            rec = {"url": url, "bytes": n, "modified": modified, "seconds": round(time.time() - t0, 2)}
            _write_meta(dest, dict(rec, fetched=time.time()))
            if log:
                log(f"[matchup] fetched {os.path.basename(dest)} {n / 1e6:.1f} MB in {time.time() - t0:.1f}s")
            return rec
        except Exception as e:
            last = e
            errlog.note("NFLM-fetch", e, path=url)
            try:
                os.remove(tmp)
            except OSError:
                pass
    raise RuntimeError(f"fetch failed: {url}: {last}")


def _open_text(path):
    if path.endswith(".gz"):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", newline="")
    return open(path, encoding="utf-8", newline="")


# ---- the season build -----------------------------------------------------------
def _new_cols():
    return {name: array.array(code) for name, code in COLUMNS}


def _unk(unknown, field, value):
    d = unknown.setdefault(field, {})
    d[value] = d.get(value, 0) + 1


def _read_participation(path, keyed, unknown):
    """Stream the participation file; for every play that is in `keyed`
    (game_id, play_id) -> row index, fill the participation columns and
    return {row index: (offense gsis ints, coverage code, mz code)} for the
    on-field tallies. Rows for plays we did not keep are skipped. Any route /
    formation / coverage / man-zone string outside the vocabularies is counted
    in `unknown` (and reads OTHER, or uncharted for coverage and man/zone)."""
    out = {}
    with _open_text(path) as fh:
        r = csv.reader(fh)
        hdr = next(r)
        ix = {h: i for i, h in enumerate(hdr)}
        c_g, c_p = ix["nflverse_game_id"], ix["play_id"]
        c_of, c_op, c_box, c_dp = ix["offense_formation"], ix["offense_personnel"], ix["defenders_in_box"], ix["defense_personnel"]
        c_nr, c_opl = ix["number_of_pass_rushers"], ix["offense_players"]
        c_ttt, c_pr, c_rt, c_mz, c_cov = ix["time_to_throw"], ix["was_pressure"], ix["route"], ix["defense_man_zone_type"], ix["defense_coverage_type"]
        rows = []
        for row in r:
            key = (row[c_g], _i(row[c_p], -1))
            i = keyed.get(key)
            if i is None:
                continue
            dl, lb, db = defense_groups(parse_personnel(row[c_dp])) if row[c_dp] else (NA, NA, NA)
            code, nwr, nol = offense_code(parse_personnel(row[c_op])) if row[c_op] else (PERS_UNKNOWN, NA, NA)
            box = _i(row[c_box], NA)
            # 2024-25 carry 0 in defenders_in_box for ~9,000 plays a season
            # where the field is simply absent; a real box is never empty.
            if box == 0:
                box = NA
            rushers = _i(row[c_nr], NA)
            ttt = _f(row[c_ttt])
            cov_s = row[c_cov].strip().upper()
            cov = COV_IX.get(cov_s)
            if cov is None:
                cov = 0
                _unk(unknown, "coverage", cov_s)
            mz_s = row[c_mz].strip().upper()
            mz = MZ_IX.get(mz_s)
            if mz is None:
                mz = 0
                _unk(unknown, "man_zone", mz_s)
            route = row[c_rt].strip().upper()
            rt = ROUTE_IX.get(route)
            if rt is None:
                rt = ROUTE_IX["OTHER"]
                _unk(unknown, "route", route)
            form = row[c_of].strip().upper()
            fm = FORM_IX.get(form)
            if fm is None:
                fm = FORM_IX["OTHER"]
                _unk(unknown, "formation", form)
            rows.append((i, dl, lb, db, box, rushers,
                         NA if ttt is None else int(round(_clamp(ttt, 0.0, 300.0) * 100)),
                         _bool(row[c_pr]), rt, mz, cov, code, nwr, nol, fm))
            pl = row[c_opl]
            if pl:
                out[i] = (tuple(gsis_int(x) for x in pl.split(";")), cov, mz)
    return rows, out


def _read_ftn(path, keyed):
    rows = []
    with _open_text(path) as fh:
        r = csv.reader(fh)
        hdr = next(r)
        ix = {h: i for i, h in enumerate(hdr)}
        c_g, c_p = ix["nflverse_game_id"], ix["nflverse_play_id"]
        want = ("is_motion", "is_play_action", "is_screen_pass", "is_rpo", "n_blitzers", "n_pass_rushers",
                "n_defense_box", "is_catchable_ball", "is_contested_ball", "is_drop", "read_thrown",
                "is_qb_out_of_pocket", "is_trick_play", "is_qb_sneak", "is_interception_worthy",
                "is_throw_away", "is_created_reception", "is_qb_fault_sack", "starting_hash", "qb_location",
                "n_offense_backfield")
        cix = [ix[w] for w in want]
        for row in r:
            i = keyed.get((row[c_g], _i(row[c_p], -1)))
            if i is None:
                continue
            v = [row[c] for c in cix]
            rd = v[10].strip().upper()
            rows.append((i, _bool(v[0]), _bool(v[1]), _bool(v[2]), _bool(v[3]), _i(v[4]), _i(v[5]), _i(v[6]),
                         _bool(v[7]), _bool(v[8]), _bool(v[9]), READ_IX.get(rd, READ_IX["OTHER"] if rd else 0),
                         _bool(v[11]), _bool(v[12]), _bool(v[13]), _bool(v[14]), _bool(v[15]), _bool(v[16]),
                         _bool(v[17]), HASH_IX.get(v[18].strip().upper(), 0), QBLOC_IX.get(v[19].strip().upper(), 0),
                         _i(v[20])))
    return rows


def build_season(season, pbp_path, participation_path=None, ftn_path=None, positions=None, log=None):
    """One season's compact store from the three files on disk (participation
    and FTN optional -- their columns read NA and `part` / `ftn` read 0).
    `positions` is {gsis int: position} for the on-field tallies (which only
    keep QB/RB/WR/TE/FB); without it every on-field player is kept.

    Returns the store dict: schema, season, built, sources, teams, games, n,
    cols, onfield, vocab (the enumerations, so a reader never needs this
    module's constants to decode a column), unknown (strings that fell into
    an OTHER bucket, with counts)."""
    t0 = time.time()
    cols = _new_cols()
    teams, team_ix = [], {}
    games, game_ix = [], {}
    keyed = {}
    unknown = {}

    def tix(ab):
        ab = (ab or "").upper()
        if ab not in team_ix:
            team_ix[ab] = len(teams)
            teams.append(ab)
        return team_ix[ab]

    with _open_text(pbp_path) as fh:
        r = csv.reader(fh)
        hdr = next(r)
        ix = {h: i for i, h in enumerate(hdr)}
        g = lambda row, c: row[ix[c]]
        n = 0
        for row in r:
            if g(row, "season_type") != "REG":
                continue
            pt = g(row, "play_type")
            if pt not in ("pass", "run", "qb_kneel", "qb_spike"):
                continue
            # A two-point try is typed pass/run with no down; it is not a
            # target or a carry in any box score and scores no fantasy
            # points under the owner's convention (nfl_matchup_profiles).
            if _i(g(row, "two_point_attempt"), 0) == 1 or _i(g(row, "play_deleted"), 0) == 1:
                continue
            off, dfn = g(row, "posteam"), g(row, "defteam")
            if not off or not dfn:
                continue
            gid = g(row, "game_id")
            if gid not in game_ix:
                game_ix[gid] = len(games)
                games.append({"id": gid, "week": _i(g(row, "week"), 0), "date": g(row, "game_date"),
                              "home": g(row, "home_team").upper(), "away": g(row, "away_team").upper(),
                              "hs": _i(g(row, "home_score"), NA), "as": _i(g(row, "away_score"), NA),
                              "kick": g(row, "start_time"), "roof": g(row, "roof"),
                              "spread": _f(g(row, "spread_line")), "total": _f(g(row, "total_line"))})
            gi = game_ix[gid]
            keyed[(gid, _i(g(row, "play_id"), -1))] = n
            if _i(g(row, "qb_kneel"), 0) == 1:
                kind = KNEEL
            elif _i(g(row, "qb_spike"), 0) == 1:
                kind = SPIKE
            elif _i(g(row, "sack"), 0) == 1:
                kind = SACK
            elif _i(g(row, "qb_scramble"), 0) == 1:
                kind = SCRAMBLE
            elif _i(g(row, "complete_pass"), 0) == 1 or _i(g(row, "incomplete_pass"), 0) == 1 or _i(g(row, "interception"), 0) == 1:
                kind = ATTEMPT
            elif _i(g(row, "rush_attempt"), 0) == 1:
                kind = RUN
            else:
                kind = OTHER_DB if _i(g(row, "qb_dropback"), 0) == 1 else RUN
            air = _f(g(row, "air_yards"))
            yac = _f(g(row, "yards_after_catch"))
            epa = _f(g(row, "epa"))
            cpoe = _f(g(row, "cpoe"))
            xpass = _f(g(row, "xpass"))
            wp = _f(g(row, "wp"))
            fuml = _i(g(row, "fumble_lost"), 0)
            vals = (
                gi, _clamp(_i(g(row, "week"), 0), 0, 255), tix(off), tix(dfn), _clamp(_i(g(row, "qtr"), 0), 0, 255),
                _clamp(_i(g(row, "down"), 0), 0, 4), _clamp(_i(g(row, "ydstogo"), 0), 0, 255),
                _clamp(_i(g(row, "yardline_100"), 0), 0, 255), _clamp(_i(g(row, "game_seconds_remaining"), 0), 0, 65535),
                _clamp(_i(g(row, "score_differential"), 0), -99, 99),
                0 if wp is None else int(round(_clamp(wp, 0.0, 1.0) * 1000)),
                kind, _i(g(row, "qb_dropback"), 0) == 1, _clamp(_i(g(row, "yards_gained"), 0), -99, 120),
                NA if air is None else int(_clamp(air, -99, 99)), NA if yac is None else int(_clamp(yac, -99, 120)),
                NA_H if epa is None else int(round(_clamp(epa, -20.0, 20.0) * 100)),
                NA_H if cpoe is None else int(round(_clamp(cpoe, -100.0, 100.0) * 10)),
                NA if xpass is None else int(round(_clamp(xpass, 0.0, 1.0) * 1000)),
                _i(g(row, "success"), NA), _i(g(row, "complete_pass"), 0), _i(g(row, "touchdown"), 0),
                _i(g(row, "pass_touchdown"), 0), _i(g(row, "rush_touchdown"), 0), _i(g(row, "interception"), 0),
                fuml, _i(g(row, "first_down"), 0), _i(g(row, "shotgun"), 0), _i(g(row, "no_huddle"), 0),
                SIDE_IX.get(g(row, "pass_location"), 0),
                {"short": 1, "deep": 2}.get(g(row, "pass_length"), 0),
                SIDE_IX.get(g(row, "run_location"), 0), GAP_IX.get(g(row, "run_gap"), 0),
                _i(g(row, "goal_to_go"), 0), _i(g(row, "qb_hit"), 0),
                gsis_int(g(row, "passer_player_id")), gsis_int(g(row, "receiver_player_id")),
                gsis_int(g(row, "rusher_player_id")),
                gsis_int(g(row, "fumbled_1_player_id")) if fuml else 0,
            )
            for (name, _c), v in zip(COLUMNS[:len(vals)], vals):
                cols[name].append(int(v))
            # participation + FTN columns start at NA and are filled below
            for name, _c in COLUMNS[len(vals):]:
                cols[name].append(0 if name in ("part", "ftn", "route", "mz", "cov", "oform", "readt")
                                  else PERS_UNKNOWN if name == "opers" else NA)
            n += 1
    t_pbp = time.time() - t0
    if log:
        log(f"[matchup] {season} pbp: {n} snaps, {len(games)} games, {t_pbp:.1f}s")

    onfield = {}
    part_n = 0
    if participation_path and os.path.exists(participation_path):
        t1 = time.time()
        rows, plays = _read_participation(participation_path, keyed, unknown)
        names = ("ndl", "nlb", "ndb", "box", "rushers", "ttt", "press", "route", "mz", "cov", "opers", "nwr", "nol", "oform")
        arrs = [cols[nm] for nm in names]
        for rec in rows:
            i = rec[0]
            cols["part"][i] = 1
            for a, v in zip(arrs, rec[1:]):
                a[i] = v
        part_n = len(rows)
        # on-field tallies per game per offensive player
        c_gi, c_db, c_kind, c_off, c_rcv = cols["gi"], cols["db"], cols["kind"], cols["off"], cols["receiver"]
        for i, (ids, cov, mz) in plays.items():
            kind = c_kind[i]
            if kind in (KNEEL, SPIKE):
                continue
            gmap = onfield.setdefault(c_gi[i], {})
            is_db = c_db[i] == 1
            is_run = kind == RUN
            target = c_rcv[i] if kind == ATTEMPT else 0
            for pid in ids:
                if not pid or (positions is not None and positions.get(pid) not in SKILL):
                    continue
                v = gmap.get(pid)
                if v is None:
                    v = gmap[pid] = array.array("H", bytes(2 * ONFIELD_W))
                    v[OF_TEAM] = c_off[i]
                v[OF_SNAPS] += 1
                if is_db:
                    v[OF_DB] += 1
                    if mz == 1:
                        v[OF_MAN] += 1
                    elif mz == 2:
                        v[OF_ZONE] += 1
                    if cov:
                        v[OF_COV0 + cov - 1] += 1
                    if pid == target:
                        v[OF_TGT] += 1
                        if mz == 1:
                            v[OF_TGT_MAN] += 1
                        elif mz == 2:
                            v[OF_TGT_ZONE] += 1
                        if cov:
                            v[OF_TCOV0 + cov - 1] += 1
                elif is_run:
                    v[OF_RUSH] += 1
        if log:
            log(f"[matchup] {season} participation: {part_n} of {n} snaps matched, {time.time() - t1:.1f}s")
    ftn_n = 0
    if ftn_path and os.path.exists(ftn_path):
        t2 = time.time()
        rows = _read_ftn(ftn_path, keyed)
        names = ("motion", "pa", "screen", "rpo", "blitzers", "frush", "fbox", "catchable", "contested", "drop",
                 "readt", "oop", "trick", "sneak", "intw", "throwaway", "created", "qbfault", "hash", "qbloc", "nbf")
        arrs = [cols[nm] for nm in names]
        for rec in rows:
            i = rec[0]
            cols["ftn"][i] = 1
            for a, v in zip(arrs, rec[1:]):
                a[i] = _clamp(int(v), -128, 127)
        ftn_n = len(rows)
        if log:
            log(f"[matchup] {season} FTN: {ftn_n} of {n} snaps matched, {time.time() - t2:.1f}s")
    return {"schema": SCHEMA, "season": int(season), "built": time.time(), "seconds": round(time.time() - t0, 2),
            "teams": teams, "games": games, "n": n, "cols": cols, "onfield": onfield,
            "matched": {"participation": part_n, "ftn": ftn_n},
            "vocab": {"coverage": COVERAGE, "mz": MZ, "routes": ROUTES, "formation": FORMATION, "read": READ,
                      "side": SIDE, "gap": GAP, "kind": KIND, "hash": HASH, "qbloc": QBLOC, "route_era": ROUTE_ERA,
                      "columns": [c for c, _t in COLUMNS]},
            "unknown": unknown, "sources": {}, "credit": dict(CREDIT),
            # route: nflreadr calls it the "primary receiver's" route. Measured
            # 2021-2025: non-blank on 98.6-99.9% of attempts WITH a receiver and
            # on 0% (2021-22) / 52-55% (2023-25) of attempts without one, so on
            # a targeted play it is the target's route; profiles read it there only.
            "notes": {"route": "targeted receiver's route on targeted attempts (NGS 'primary receiver')",
                      "coverage": "charted on pass attempts (99.8%); sacks/scrambles only from 2023; never runs"}}


def read_snap_counts(path):
    """nflverse snap_counts_<Y>.csv (Pro Football Reference's counts) ->
    {(game_id, normalised name, team): (offense snaps, offense share)} for the
    regular season. The one snap source for 2026, which has no participation
    file; keyed by name because PFR ids are not in the gsis crosswalk."""
    out = {}
    with _open_text(path) as fh:
        for row in csv.DictReader(fh):
            if (row.get("game_type") or "REG") != "REG":
                continue
            snaps = _i(row.get("offense_snaps"), 0)
            pct = _f(row.get("offense_pct"))
            if snaps <= 0 and not pct:
                continue
            out[(row.get("game_id") or "", norm_name(row.get("player") or ""), (row.get("team") or "").upper())] = (
                snaps, 0.0 if pct is None else pct)
    return out


# ---- the id crosswalk -----------------------------------------------------------
def build_ids(players_path, dp_path=None, log=None):
    """{gsis int: {"name", "norm", "pos", "sleeper", "espn", "team", "status"}}
    for every QB/RB/WR/TE/FB in nflverse players.csv, Sleeper ids (and a
    second ESPN id) joined from DynastyProcess by gsis id. Measured 2026-10-23:
    of the 628 skill players with 20+ plays in 2023-2026, all 628 have a
    Sleeper id through DynastyProcess; players.csv alone has espn_id for 986
    of 987 and no sleeper id at all. DynastyProcess files FB as RB and lists
    ten gsis ids twice (the later row wins); positions are taken from
    players.csv."""
    out = {}
    with open(players_path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            pid = gsis_int(row.get("gsis_id"))
            pos = (row.get("position") or "").upper()
            if not pid or pos not in SKILL:
                continue
            name = row.get("display_name") or f"{row.get('first_name', '')} {row.get('last_name', '')}".strip()
            out[pid] = {"name": name, "norm": norm_name(name), "pos": pos, "sleeper": "",
                        "espn": str(row.get("espn_id") or ""), "team": row.get("latest_team") or "",
                        "status": row.get("status") or "", "last": _i(row.get("last_season"), 0)}
    dp_n = 0
    if dp_path and os.path.exists(dp_path):
        with open(dp_path, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                pid = gsis_int(row.get("gsis_id"))
                if not pid:
                    continue
                rec = out.get(pid)
                if rec is None:
                    pos = (row.get("position") or "").upper()
                    if pos not in SKILL:
                        continue
                    name = row.get("name") or ""
                    rec = out[pid] = {"name": name, "norm": norm_name(name), "pos": pos, "sleeper": "",
                                      "espn": "", "team": row.get("team") or "", "status": "", "last": 0}
                sl = (row.get("sleeper_id") or "").strip()
                if sl and sl != "NA":
                    rec["sleeper"] = sl
                    dp_n += 1
                es = (row.get("espn_id") or "").strip()
                if es and es != "NA" and not rec["espn"]:
                    rec["espn"] = es
    if log:
        log(f"[matchup] ids: {len(out)} skill players, {dp_n} with Sleeper ids")
    return out


def ids_index(ids):
    """Lookups over build_ids' map: {"sleeper": {sleeper_id: gsis}, "espn":
    {espn_id: gsis}, "norm": {norm name: [gsis, ...]}}."""
    sl, es, nm = {}, {}, {}
    for pid, r in ids.items():
        if r.get("sleeper"):
            sl[r["sleeper"]] = pid
        if r.get("espn"):
            es[r["espn"]] = pid
        nm.setdefault(r["norm"], []).append(pid)
    return {"sleeper": sl, "espn": es, "norm": nm}


def resolve_name(norm, idx, ids, team=None, pos=None):
    """The gsis id for a normalised name, disambiguated by team and position
    when several players share it; None when none or still ambiguous."""
    cands = idx["norm"].get(norm) or []
    if len(cands) > 1 and team:
        c2 = [c for c in cands if (ids[c].get("team") or "").upper() == team.upper()]
        cands = c2 or cands
    if len(cands) > 1 and pos:
        c2 = [c for c in cands if ids[c].get("pos") == pos or (pos == "RB" and ids[c].get("pos") == "FB")]
        cands = c2 or cands
    if len(cands) > 1:
        # the player still active most recently
        cands = sorted(cands, key=lambda c: -ids[c].get("last", 0))
        if len(cands) > 1 and ids[cands[0]].get("last", 0) == ids[cands[1]].get("last", 0):
            return None
    return cands[0] if cands else None


# ---- store access ---------------------------------------------------------------
def load_season(season):
    """The compact season store from the deep store, or None."""
    payload, _ts = deep_cache.load(KEY.format(season=int(season)))
    if not payload or payload.get("schema") != SCHEMA:
        return None
    return payload


def load_ids():
    payload, _ts = deep_cache.load(IDS_KEY)
    return payload if payload and payload.get("schema") == SCHEMA else None


def plays(store, i):
    """One play as {column: value} (decoded codes stay ints; see `vocab`)."""
    return {name: store["cols"][name][i] for name, _t in COLUMNS}


def game_weeks(store):
    """{week: [game index, ...]} for a store."""
    out = {}
    for gi, g in enumerate(store["games"]):
        out.setdefault(g["week"], []).append(gi)
    return out


# ---- the refresh (a PC-first deep job's body) -----------------------------------
def default_seasons(season, month):
    """The seasons a daily refresh reads: the current one, and the previous
    one until October -- its participation file lands in September (the 2024
    file is dated 2025-09-04) and nflverse corrects pbp through the summer."""
    return [season] if month >= 10 else [season - 1, season]


def refresh(seasons=None, raw_dir=None, keep_raw=True, log=None, max_age_s=None):
    """Download what is newer than what is on disk and rebuild those seasons
    plus the id crosswalk. `seasons` defaults to default_seasons(). A file on
    disk is re-downloaded when upstream's Last-Modified differs from the one
    recorded at its fetch (one HEAD each; `max_age_s` skips even the HEAD for
    a file younger than that); a season none of whose files changed, whose
    store exists at the current SCHEMA, is not rebuilt. `raw_dir` holds the
    downloads (default: <deep store>/nfl_matchup_raw).

    Hook (NOT wired here, CLAUDE.md: nothing CPU-bound in a web process): a
    deep_cache job `nfl_matchup` registered from app.py that returns DEFERRED
    when the PC is on (deep_cache.request_pc) and otherwise runs this under
    deep_cache.HEAVY_BUILD in a niced child; pc_worker runs the same function
    and uploads nfl_matchup_<season>.pkl and nfl_matchup_ids.pkl through
    /api/art/upload. Cadence: daily in season (nflverse rebuilds pbp within
    hours of a game and FTN a day or two later). artifacts.SCHEMA must be
    bumped when these pickles ship.

    Returns {"seasons": {season: summary}, "ids": summary, "seconds"}."""
    t0 = time.time()
    raw_dir = raw_dir or os.path.join(deep_cache.CACHE_DIR, "nfl_matchup_raw")
    os.makedirs(raw_dir, exist_ok=True)
    if seasons is None:
        import clock
        seasons = default_seasons(_current_season(), clock.today_et().month)
    summary = {"seasons": {}, "ids": None}
    # ids first: the on-field tallies keep skill positions only
    p_players = os.path.join(raw_dir, "players.csv")
    p_dp = os.path.join(raw_dir, "db_playerids.csv")
    src_ids = {}
    for kind, path in (("players", p_players), ("dp_ids", p_dp)):
        if _fresh(path, url_for(kind), max_age_s):
            src_ids[kind] = dict(_read_meta(path), url=url_for(kind), bytes=os.path.getsize(path), cached=True)
            continue
        try:
            src_ids[kind] = fetch(url_for(kind), path, log=log)
        except Exception as e:
            errlog.note("NFLM-refresh-ids", e, path=kind)
    ids = None
    if os.path.exists(p_players):
        ids = build_ids(p_players, p_dp if os.path.exists(p_dp) else None, log=log)
        deep_cache.save(IDS_KEY, {"schema": SCHEMA, "built": time.time(), "ids": ids, "sources": src_ids})
        summary["ids"] = {"n": len(ids), "sleeper": sum(1 for r in ids.values() if r["sleeper"])}
    positions = {pid: r["pos"] for pid, r in (ids or {}).items()} if ids else None
    for season in seasons:
        season = int(season)
        paths, sources = {}, {}
        changed = False
        for kind, ok in (("pbp", season in PBP_SEASONS), ("participation", season in PARTICIPATION_SEASONS),
                         ("ftn", season in FTN_SEASONS)):
            if not ok:
                continue
            url = url_for(kind, season)
            path = os.path.join(raw_dir, os.path.basename(url))
            if _fresh(path, url, max_age_s):
                paths[kind] = path
                sources[kind] = dict(_read_meta(path), url=url, bytes=os.path.getsize(path), cached=True)
                continue
            try:
                sources[kind] = fetch(url, path, log=log)
                paths[kind] = path
                changed = True
            except Exception as e:
                errlog.note("NFLM-refresh", e, path=f"{season} {kind}")
                if kind == "pbp":
                    break
        if "pbp" not in paths:
            summary["seasons"][season] = {"error": "no play-by-play"}
            continue
        if not changed and load_season(season) is not None:
            summary["seasons"][season] = {"skipped": "no source changed and the store is current"}
            if log:
                log(f"[matchup] {season}: unchanged upstream, store kept")
            continue
        store = build_season(season, paths["pbp"], paths.get("participation"), paths.get("ftn"),
                             positions=positions, log=log)
        store["sources"] = sources
        deep_cache.save(KEY.format(season=season), store)
        summary["seasons"][season] = {"n": store["n"], "games": len(store["games"]), "matched": store["matched"],
                                      "seconds": store["seconds"]}
        if not keep_raw:
            for p in paths.values():
                try:
                    os.remove(p)
                except OSError:
                    pass
    summary["seconds"] = round(time.time() - t0, 1)
    return summary


def _current_season():
    """The NFL season a date belongs to: the year, until March."""
    import clock
    d = clock.today_et()
    return d.year if d.month >= 3 else d.year - 1
