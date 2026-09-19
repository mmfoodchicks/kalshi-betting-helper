"""Year-round NFL draft consensus + live status (Sleeper) for the fantasy sim.

The season Monte Carlo in nfl_sim projects off PAST production, so by design it
can't see a player's EXPECTED 2026 role. Three groups get mis-rated as a result:

  - free agents / new teams (Tyreek Hill, Joe Mixon, Deebo Samuel): the stat line
    we average is from their old team / old role.
  - injury returners (Tank Dell, Zach Charbonnet): the recent seasons are zeros
    or a tiny role, so the raw average buries a player who should bounce back.
  - role-change guys generally: a backup promoted to a starter has no starter
    stats yet.

Sleeper's public players feed carries a year-round consensus draft rank
(`search_rank`) plus LIVE team and injury status -- exactly the forward-looking,
role-aware signal our production model lacks, and it is populated all offseason
(unlike ESPN's fantasy board, which isn't built until training camp). We blend
each player's model value toward this consensus and flag free agents / new teams
/ injury returns, so the draft board is trustworthy on exactly those players. As
the consensus and injury feeds update through the summer, the board self-corrects
-- the same recursive/dynamic behavior as the rest of the simulators.
"""

import gzip
import http.client
import json
import os
import re
import time
import unicodedata
import urllib.request

import errlog

_URL = "https://api.sleeper.app/v1/players/nfl"
_POS = {"QB", "RB", "WR", "TE"}
# Offseason injury buckets that mean "returning / role in doubt" (vs a routine Q).
_RETURN = {"IR", "PUP", "Out", "Doubtful", "NA", "Sus", "DNR", "COV"}

# ESPN (our model) vs Sleeper abbreviation divergences, canonicalized for the
# new-team comparison so we don't false-flag e.g. WSH vs WAS as a team change.
_TEAM_CANON = {"WSH": "WAS", "JAC": "JAX", "LA": "LAR", "OAK": "LV", "SD": "LAC",
               "STL": "LAR", "ARZ": "ARI", "CLV": "CLE", "HST": "HOU", "BLT": "BAL"}


def _canon_team(t):
    t = (t or "").upper()
    return _TEAM_CANON.get(t, t)


def _fetch_players_once():
    """One attempt at Sleeper's full players blob. The feed is unfiltered (~12MB)
    so we request gzip (~2.5MB) to avoid the body truncation large responses hit
    behind a proxy; reads in chunks and salvages IncompleteRead's partial."""
    req = urllib.request.Request(_URL, headers={
        "User-Agent": "vigil/1.0", "Accept": "application/json",
        "Accept-Encoding": "gzip"})
    buf = bytearray()
    with urllib.request.urlopen(req, timeout=60) as resp:
        gz = "gzip" in (resp.headers.get("Content-Encoding") or "").lower()
        while True:
            try:
                chunk = resp.read(65536)
            except http.client.IncompleteRead as e:
                buf += e.partial
                break
            if not chunk:
                break
            buf += chunk
    if gz:
        try:
            raw = gzip.decompress(bytes(buf))
        except Exception:
            raw = _inflate_partial(bytes(buf))   # truncated stream -> decompress prefix
    else:
        raw = bytes(buf)
    text = raw.decode("utf-8", "ignore")
    try:
        return json.loads(text)
    except ValueError:
        return _salvage_players(text)            # truncated body -> keep what we got


def _inflate_partial(raw_gzip):
    """Inflate as much of a truncated gzip stream as possible."""
    import zlib
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = bytearray()
    for i in range(0, len(raw_gzip), 65536):
        try:
            out += d.decompress(raw_gzip[i:i + 65536])
        except zlib.error:
            break
    return bytes(out)


def _salvage_players(text):
    """Parse a truncated Sleeper blob ('{"id":{...},"id2":{...},...') by walking the
    brace depth (string-aware) to the last point a TOP-LEVEL player object closed,
    then closing the JSON there. We lose the tail (a random id-keyed slice) but keep
    the bulk -- far better than an empty pool when a proxy/CDN truncates the body."""
    depth = 0
    in_str = False
    esc = False
    last_complete = None
    for i, ch in enumerate(text):
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 1:                 # a top-level player object just closed
                last_complete = i
    if last_complete is None:
        return {}
    try:
        return json.loads(text[:last_complete + 1] + "}")
    except ValueError:
        return {}


def _fetch_players():
    """Resilient fetch: the proxy truncates the big response intermittently, so
    retry a few times before giving up (a retry almost always lands intact)."""
    import time
    last = None
    for attempt in range(4):
        try:
            return _fetch_players_once()
        except Exception as e:                  # truncated body / transient network
            last = e
            time.sleep(1.0 * (attempt + 1))
    raise last


def _norm(name):
    """Match key across ESPN displayName and Sleeper full_name: drop accents,
    suffixes (Jr/Sr/II..), punctuation, case."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", " ", s.lower())
    s = re.sub(r"[^a-z ]", " ", s)
    return " ".join(s.split())


# ---- the roster the depth-chart gate consults ---------------------------------
# A failed or truncated fetch used to become an authoritative EMPTY roster:
# consensus() answered the exception with {} and racing._cached stored that for
# twelve hours, so one bad blob switched nfl_dfs._apply_depth's gate off for
# every board the worker built until then, with no ledger row (the NFLD-depth
# note fired only if consensus() raised, which it never did). Found 2026-09-18
# while pinning the roster for the football A/B and verified by behaviour: two
# calls, one attempt, {} cached at 43,200 s, a practice-squad name through the
# gate. The contract agreed with the reviewer on 2026-09-19:
#   * a failure or a malformed response is never an empty roster;
#   * a failed result never enters the success cache, and Sleeper is tried
#     again after ROSTER_RETRY_S rather than masked for twelve hours;
#   * a build may use a last-known-good copy up to ROSTER_MAX_AGE_S old with
#     its source and age stamped on the board, or it refuses with a ledger
#     event (nfl_dfs.RosterUnavailable) -- the gate is never silently off;
#   * research stays pinned to an immutable capture (research.sd_board.feeds).
ROSTER_TTL_S = 12 * 3600        # a copy is current for this long (it tracks news / injuries)
ROSTER_MAX_AGE_S = 36 * 3600    # far from lock: older than this, a last-known-good copy is refused
ROSTER_RETRY_S = 300            # after a failure, no new attempt sooner than this
# Near lock the allowance is the WINDOW's, not 36 hours (the reviewer's point of
# 2026-09-19): a valid roster captured Monday morning, Sleeper down, the
# mandatory T-25m rebuild at 20 hours of age would otherwise produce a freshly
# built, correctly stamped board from a roster old enough to miss the single
# late inactive that changes thousands of Showdown combinations. Observability
# does not make stale input safe. The minute thresholds are pc_worker's
# pre-lock refresh windows (_PRELOCK_WINDOWS: T-4h, T-2h, T-1h, T-25m), pinned
# equal by a guard, so a mandatory freshness check cannot be satisfied by data
# older than the window's purpose: inside a window the roster must be younger
# than the window is long.
ROSTER_LOCK_WINDOWS = ((240, 4 * 3600), (120, 2 * 3600), (60, 3600), (25, 25 * 60))


def roster_max_age_for(seconds_to_lock):
    """The oldest last-known-good roster a build this far from lock may run on.
    Unknown lock time (None) reads as far from lock."""
    if seconds_to_lock is None:
        return ROSTER_MAX_AGE_S
    mins = float(seconds_to_lock) / 60.0
    allowed = ROSTER_MAX_AGE_S
    for hi, age in ROSTER_LOCK_WINDOWS:
        if mins <= hi:
            allowed = age
    return allowed
_roster = {"data": None, "fetched": 0.0, "failed": 0.0, "error": None, "source": None}
_PINNED = None                  # research only: (records, stamp) from a captured file


def _lkg_path():
    """Beside the other shared stores (boardshare): the data disk when there is
    one, so a fresh slate subprocess and a restarted worker start from the last
    good roster instead of a 12 MB fetch, and every process gates on one copy."""
    return os.path.join(os.environ.get("VIGIL_SIM_CACHE_DIR") or os.environ.get("DEEP_CACHE_DIR") or "/tmp",
                        "roster_lkg.json")


def _save_lkg(data, ts):
    try:
        path = _lkg_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as fh:
            json.dump({"fetched": float(ts), "records": len(data), "data": data}, fh)
        os.replace(tmp, path)
    except Exception as e:
        errlog.note("ADP-lkg-write", e, path=_lkg_path())


def _load_lkg():
    """(data, fetched_ts) from the shared store, or (None, 0.0)."""
    try:
        path = _lkg_path()
        if not os.path.exists(path):
            return None, 0.0
        with open(path) as fh:
            d = json.load(fh)
        data = d.get("data") or None
        return (data, float(d.get("fetched") or 0.0)) if data else (None, 0.0)
    except Exception as e:
        errlog.note("ADP-lkg-read", e, path=_lkg_path())
        return None, 0.0


def _state(st, now, source, max_age_s=ROSTER_MAX_AGE_S, seconds_to_lock=None):
    fetched = float(st.get("fetched") or 0.0)
    return {"source": source, "records": len(st["data"]) if st.get("data") else 0,
            "fetched_utc": (time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(fetched)) if fetched else None),
            "age_s": (int(now - fetched) if fetched else None),
            "ttl_s": ROSTER_TTL_S, "max_age_s": int(max_age_s),
            "seconds_to_lock": (None if seconds_to_lock is None else int(seconds_to_lock)),
            "error": st.get("error")}


def roster(now=None, seconds_to_lock=None):
    """(records, state): the roster the depth-chart gate may consult, or
    (None, state) when no copy is trustworthy. `records` is the
    {norm_name: row} map consensus() always produced; `state` says where it
    came from (pinned / live / disk / last-known-good / unavailable), when it
    was fetched, how old it is and how old it was allowed to be, so a board
    can carry that stamp. `seconds_to_lock` sets the allowance
    (roster_max_age_for); a fresh copy inside the TTL is always fine."""
    now = time.time() if now is None else float(now)
    max_age_s = roster_max_age_for(seconds_to_lock)
    if _PINNED is not None:
        recs, stamp = _PINNED
        return recs, {"source": "pinned", "records": len(recs), "age_s": 0, "error": None,
                      "max_age_s": None, "seconds_to_lock": None, **stamp}
    st = _roster
    if st["data"] is None:                      # cold process: the shared last-known-good first
        data, ts = _load_lkg()
        if data:
            st.update(data=data, fetched=ts, source="disk")
    if st["data"] is not None and now - st["fetched"] < min(ROSTER_TTL_S, max_age_s):
        return st["data"], _state(st, now, st.get("source") or "live", max_age_s, seconds_to_lock)
    if now - st["failed"] >= ROSTER_RETRY_S:
        try:
            data = _build(_fetch_players())
            st.update(data=data, fetched=now, failed=0.0, error=None, source="live")
            _save_lkg(data, now)
            return data, _state(st, now, "live", max_age_s, seconds_to_lock)
        except Exception as e:
            st["failed"], st["error"] = now, f"{type(e).__name__}: {e}"
            errlog.note("ADP-players", e, msg="Sleeper roster fetch failed; the depth-chart gate uses a "
                                              "last-known-good copy within its lock-relative allowance or refuses")
    if st["data"] is not None and now - st["fetched"] < max_age_s:
        return st["data"], _state(st, now, "last-known-good", max_age_s, seconds_to_lock)
    return None, _state(st, now, "unavailable", max_age_s, seconds_to_lock)


def consensus():
    """{norm_name: row} for the callers that only want a rank map (best-ball
    value, preseason usage, ADP blending): the roster when one is trustworthy,
    else {} -- those callers already treat {} as "no ranks". The depth-chart
    gate does NOT come through here; it reads roster() and refuses on None."""
    return roster()[0] or {}


def _build(d):
    """{norm_name: {rank, team, pos, injury, injury_return, fa, years_exp,
    status, depth_pos, depth, active}} for skill-position players, from
    Sleeper's raw players blob. Raises on an empty or malformed blob: that is a
    failed fetch, not a roster with nobody on it."""
    if not isinstance(d, dict):
        raise ValueError(f"Sleeper players blob is {type(d).__name__}, not a dict")
    out = {}
    for _pid, p in d.items():
        if not isinstance(p, dict):
            continue
        if p.get("position") not in _POS:
            continue
        nm = _norm(p.get("full_name") or "")
        if not nm:
            continue
        sr = p.get("search_rank")
        sr = sr if isinstance(sr, (int, float)) and sr > 0 else None
        inj = (p.get("injury_status") or "").strip()
        team = (p.get("team") or "").strip() or None
        row = {"rank": sr, "team": team, "pos": p.get("position"),
               "name": p.get("full_name"),
               "injury": inj or None, "injury_return": inj in _RETURN,
               "fa": team is None, "years_exp": p.get("years_exp"),
               "status": p.get("status"),
               # Sleeper's depth chart: slot (LWR/RWR/SWR/RB/TE/QB) and
               # order within it (1 = starter). A practice-squad player
               # reads as Active with NO entry -- that absence is the
               # DFS builder's roster gate (nfl_dfs._depth_verdict).
               "depth_pos": p.get("depth_chart_position"),
               "depth": p.get("depth_chart_order"),
               "active": p.get("active")}
        # Two players can normalize to the same key (rare); keep the better
        # (lower) consensus rank so a star isn't shadowed by a namesake scrub.
        prev = out.get(nm)
        if prev is None or (sr is not None and (prev["rank"] is None or sr < prev["rank"])):
            out[nm] = row
    if not out:
        raise ValueError("Sleeper players blob carried no skill-position records")
    return out


_GAMES = 17
_INJECT_MAX_RANK = 240     # only add genuinely draftable consensus players


def inject(rows):
    """Add draftable consensus players the production pool is MISSING. The model
    pool is built from multi-year statistical leaders, so small-role guys who are
    expected to start (Zach Charbonnet, Tank Dell, a promoted backup) never appear
    -- worse than being mis-rated. For each missing consensus player inside the
    draftable range we synthesize a row, projecting their season off the model's
    own per-position production curve at the depth their consensus rank implies.
    Returns rows (mutated). Call BEFORE value/VOR is computed."""
    con = consensus()
    if not con or not rows:
        return rows
    have = {_norm(r.get("name") or "") for r in rows}
    # Model's per-position season curve (sorted desc) = the role-by-depth ladder.
    by_pos = {}
    for r in rows:
        by_pos.setdefault(r["pos"], []).append(r["season"])
    for v in by_pos.values():
        v.sort(reverse=True)
    # Walk consensus by rank so each position's missing players slot in at the
    # right depth (Nth WR off the board -> the model's Nth-best WR production).
    ranked = sorted(((c["rank"], nm, c) for nm, c in con.items() if c.get("rank")),
                    key=lambda x: x[0])
    depth = {}
    for rank, nm, c in ranked:
        pos = c.get("pos")
        if pos not in by_pos and pos not in ("QB", "RB", "WR", "TE"):
            continue
        k = depth.get(pos, 0)
        depth[pos] = k + 1
        if nm in have or rank > _INJECT_MAX_RANK:
            continue
        # Synthesized rows need a real, rostered, active player behind them.
        # Sleeper's search_rank keeps famous RETIRED names high (people still
        # search them), so without this gate Todd Gurley materializes at a
        # top-50 board spot with a projection invented from thin air. A
        # production-pool player who's merely unsigned keeps his row (his
        # stats are real and the FA badge shows); a consensus-only ghost with
        # no team or non-Active status must not.
        if c.get("fa") or (c.get("status") or "Active") != "Active":
            continue
        curve = by_pos.get(pos) or [80.0]
        season = curve[min(len(curve) - 1, k)]
        fppg = season / _GAMES
        row = {
            "id": "cs_" + nm.replace(" ", "_"), "name": c.get("name") or _title(nm), "pos": pos,
            "team": c.get("team"), "season": round(season, 1),
            "fppg": round(fppg, 1), "floor": round(fppg * 0.5, 1),
            "ceiling": round(fppg * 1.45, 1), "boom": round(fppg * 2.0, 1),
            "rookie": False, "consensus_rank": int(rank), "proj_source": "consensus",
        }
        if c.get("fa"):
            row["fa"] = True
        if c.get("injury"):
            row["injury"] = c["injury"]
        if c.get("injury_return"):
            row["injury_return"] = True
        rows.append(row)
    return rows


def _title(nm):
    return " ".join(w.capitalize() for w in (nm or "").split())


def blend(rows):
    """Blend each production-based row's `value` toward the Sleeper consensus and
    attach forward-looking flags, in place. Anchors a player to "what a
    consensus-rank-R player is worth" on our own value scale, so FA / new-team /
    injury-return guys stop being buried by their past stat line. Flagged players
    lean harder on consensus (the role question our sim can't answer); everyone
    else keeps a lighter pull so the model's edges survive. Players injected from
    consensus already sit at their consensus-implied value, so they're flagged but
    not re-blended. Call AFTER value/VOR is computed. No-ops if Sleeper is down."""
    con = consensus()
    if not con or not rows:
        return rows
    vals = sorted((r["value"] for r in rows), reverse=True)
    n = len(vals)

    def value_at_rank(rank):
        if n == 0:
            return 0.0
        return vals[min(n - 1, max(0, int(round(rank)) - 1))]

    for r in rows:
        c = con.get(_norm(r.get("name") or ""))
        if not c:
            continue
        flagged = False
        steam = c.get("team")
        if c.get("fa"):
            r["fa"] = True
            flagged = True
        elif steam and r.get("team") and _canon_team(steam) != _canon_team(r["team"]):
            r["new_team"] = True
            r["sleeper_team"] = steam
            flagged = True
        if c.get("injury"):
            r["injury"] = c["injury"]
        if c.get("injury_return"):
            r["injury_return"] = True
            flagged = True
        if c.get("rank"):
            r["consensus_rank"] = int(c["rank"])
            if r.get("proj_source") == "consensus":
                continue                              # already consensus-anchored
            tgt = value_at_rank(c["rank"])
            w_model = 0.35 if flagged else 0.60       # flagged -> lean on consensus
            r["value"] = round(w_model * r["value"] + (1 - w_model) * tgt, 1)
    return rows
