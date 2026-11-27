"""The Pick6 PAPER ledger (2026-11-27; the owner's backlog item 7,
research/reports/sprint_2026_11_26.md row 7: "DraftKings has no authorized
Pick6 line feed, so a manual entry workflow is the feasible path").

The owner types an entry exactly as DraftKings shows it -- the sport, the
picks (player, stat, line, More/Less), the entry type and the payout schedule
DraftKings states on the entry, the (paper) stake. Nothing here is scraped:
Pick6 is peer-to-peer (prize pools, not house odds), so the only payout figure
worth recording is the one DraftKings printed on the entry. Each pick keeps
Vigil's own probability for that side at the moment of entry, from the board
the Pick 6 views draw (`model_prob`), or says why there is none. After the
game each pick is graded off the box score the app already reads elsewhere
(Sleeper's weekly stats -- the DFS look-back's source; MLB's statsapi box --
the prop recorder's; ESPN's NBA box -- the Matchups engine's parser), under
DraftKings' push/void rules as far as a source we could read states them
(`RULES`, shown on the card), and the entry's paper result comes from the
owner's schedule. The record is SQL sums over per-row scores written at grade
time (`record`): a read that grows with the table never fetches its rows.

Two tables (created on first use, the dfslog pattern), in the app's ledger
file (store.DB_PATH):

  pick6_entries  one row an entry: the schedule as typed, the stake, the
                 settled payout (or why it waits)
  pick6_picks    one row a pick: Vigil's figure at entry, the grade, the
                 actual stat, and the scores (won / log loss / Brier) the
                 record sums
"""

import datetime
import json
import math
import re
import threading
import time
import unicodedata

import errlog
import store

# DraftKings' own pick-count limits ("a minimum of 2-picks in your pick set and
# a maximum of six picks" -- FantasyLife's guide; RotoGrinders: "at least two
# different players").
MIN_PICKS, MAX_PICKS = 2, 6

# A final stat EXACTLY equal to the line is VOID, More or Less: DraftKings'
# Pick6 rules pages (pick6.draftkings.com/pick6-rules-and-scoring-nfl, -nba,
# -mlb), read by the owner on 2026-11-27 -- they answer an automated read with
# 403, so until then the ledger scored a tie as a loss, the conservative
# reading. "loss" is still accepted here, for a sport DraftKings ever rules so.
TIE_RULE = "void"

# DraftKings' Pick6 Pardon (pick6.draftkings.com/pardon-policy, stated by
# DraftKings, read by the owner 2026-11-27). It applies to UNSUCCESSFUL More
# selections only, on full-game player-stat projections only (First, Longest,
# Game High and other event-based markets excluded), regular season and
# postseason, live full-game selections included; a qualifying selection is
# VOIDED; no proof of injury is needed; a successful More pick keeps its grade.
# Who qualifies, by sport -- the rules as data; `_pardon` decides from them only
# what a box ESTABLISHES and sends the rest to review, naming what is missing:
PARDON = {
    "nfl": {"eligible": ("QB", "RB", "WR", "TE", "FB"), "excluded": "defensive players and kickers",
            "exit": "an offensive player who records a first-half snap and no snaps afterward (special-teams "
                    "snaps count as snaps)"},
    "nba": {"exit": "a player who participates during the first half and records no playing time afterward"},
    "wnba": {"exit": "a player who participates during the first half and records no playing time afterward"},
    "mlb": {"blocks": ("bat",), "excluded": "pitchers",
            "exit": "a hitter who records exactly one plate appearance"},
    "nhl": {"excluded": "goalies",
            "exit": "a skater who participates in the first period and records no ice time afterward"},
}
# The market kinds a pick may be. Only "full" (a full-game player stat) can be
# pardoned; every registry stat is one. A hand-typed stat names its kind.
KINDS = {"full": "Full-game player stat", "event": "Event-based (First, Longest, Game High...)",
         "part": "Part of the game (a half, a quarter, a period...)"}

# Vigil's figure is attached only off a board built within this many seconds of
# the entry: the views serve boards on a 30-minute TTL and the PC rebuilds them
# on its own clock, so six hours is "the board the view was showing", never
# last week's.
_MODEL_MAX_AGE_S = 6 * 3600

# How many outside calls one grading pass may make (a schedule plus one box a
# game for a baseball or basketball date; one call a week for football). A
# backlog is worked through over several passes instead of one burst.
_MAX_FETCH = 40
_GRADE_EVERY_S = 3600
_state = {"last_grade": 0.0}
_grade_lock = threading.Lock()

# The stats the auto-graders read. Each: (label, box block, fields summed[,
# special]). NFL's block is Sleeper's weekly `stats` dict (the keys dfslog and
# dfs_hindsight read); MLB's the statsapi box's batting / pitching blocks
# (pitcher strikeouts are their own stat: a batter's strikeouts are a
# different market); NBA's nba_matchup_data.parse_box's row. `special` marks a
# stat that can come WITHOUT the standard participation -- runs and stolen
# bases (a pinch runner scores or steals with no plate appearance), and H+R+RBI
# through its runs. DraftKings says certain specialized categories have
# exceptions to the standard requirement and the owner did not list them, so a
# borderline appearance (in the box, no plate appearance) on one of these goes
# to manual review instead of the standard void.
_NFL = {
    "pass_yd": ("Passing yards", "", ("pass_yd",)),
    "pass_td": ("Passing TDs", "", ("pass_td",)),
    "pass_att": ("Pass attempts", "", ("pass_att",)),
    "pass_cmp": ("Pass completions", "", ("pass_cmp",)),
    "pass_int": ("Interceptions thrown", "", ("pass_int",)),
    "rush_yd": ("Rushing yards", "", ("rush_yd",)),
    "rush_att": ("Rush attempts", "", ("rush_att",)),
    "rec": ("Receptions", "", ("rec",)),
    "rec_yd": ("Receiving yards", "", ("rec_yd",)),
    "rush_rec_yd": ("Rush + rec yards", "", ("rush_yd", "rec_yd")),
    "pass_rush_yd": ("Pass + rush yards", "", ("pass_yd", "rush_yd")),
}
_MLB = {
    "hit": ("Hits", "bat", ("hits",)),
    "tb": ("Total bases", "bat", ("totalBases",)),
    "hr": ("Home runs", "bat", ("homeRuns",)),
    "sb": ("Stolen bases", "bat", ("stolenBases",), True),
    "run": ("Runs", "bat", ("runs",), True),
    "rbi": ("RBIs", "bat", ("rbi",)),
    "hrr": ("Hits + runs + RBIs", "bat", ("hits", "runs", "rbi"), True),
    "bb": ("Walks (batter)", "bat", ("baseOnBalls",)),
    "p_k": ("Pitcher strikeouts", "pit", ("strikeOuts",)),
    "p_outs": ("Pitching outs", "pit", ("outs",)),
    "p_h": ("Hits allowed", "pit", ("hits",)),
    "p_er": ("Earned runs allowed", "pit", ("earnedRuns",)),
}
_NBA = {
    "pts": ("Points", "", ("pts",)),
    "reb": ("Rebounds", "", ("reb",)),
    "ast": ("Assists", "", ("ast",)),
    "tpm": ("3-pointers made", "", ("tpm",)),
    "pra": ("Pts + reb + ast", "", ("pts", "reb", "ast")),
    "pr": ("Pts + reb", "", ("pts", "reb")),
    "pa": ("Pts + ast", "", ("pts", "ast")),
    "ra": ("Reb + ast", "", ("reb", "ast")),
    "stl": ("Steals", "", ("stl",)),
    "blk": ("Blocks", "", ("blk",)),
    "stl_blk": ("Steals + blocks", "", ("stl", "blk")),
    "tov": ("Turnovers", "", ("tov",)),
}
# League of Legends: Vigil prices these three (lol.py's Pick 6 board); nothing
# here reads a LoL box, so they are graded by the owner's typed stat.
_LOL = {"kills": ("Kills", None, ()), "assists": ("Assists", None, ()), "cs": ("CS", None, ())}

# Every sport DraftKings runs Pick6 on that the owner may enter. `grader` None:
# the owner types the final stat and the same rules grade it.
SPORTS = {
    "nfl": {"label": "NFL", "stats": _NFL, "grader": "sleeper"},
    "nba": {"label": "NBA", "stats": _NBA, "grader": "espn"},
    "mlb": {"label": "MLB", "stats": _MLB, "grader": "mlb"},
    "lol": {"label": "League of Legends", "stats": _LOL, "grader": None},
    "nhl": {"label": "NHL", "stats": {}, "grader": None},
    "cfb": {"label": "College football", "stats": {}, "grader": None},
    "cbb": {"label": "College basketball", "stats": {}, "grader": None},
    "wnba": {"label": "WNBA", "stats": {}, "grader": None},
    "soccer": {"label": "Soccer", "stats": {}, "grader": None},
    "golf": {"label": "Golf", "stats": {}, "grader": None},
    "tennis": {"label": "Tennis", "stats": {}, "grader": None},
    "mma": {"label": "MMA", "stats": {}, "grader": None},
    "other": {"label": "Other", "stats": {}, "grader": None},
}
_SOURCE = {"sleeper": "Sleeper's weekly box score", "mlb": "MLB's statsapi box score",
           "espn": "ESPN's box score"}

# The rules the grader implements, each with its basis (DraftKings' own pages
# as the owner read them, or this ledger's rule) and its source. The card
# draws this list; the guards pin each id beside the code that implements it.
_DK_CONFIRMED = "stated by DraftKings, confirmed by the owner 2026-11-27"
_DK_PAGES = ("pick6.draftkings.com/pick6-rules-and-scoring-nfl, -nba and -mlb, read by the owner 2026-11-27 "
             "(they refuse an automated read)")
RULES = [
    {"id": "played", "verified": True, "basis": _DK_CONFIRMED,
     "text": "Minimum participation: NFL one snap; NBA one second of official playing time; MLB batting one "
             "plate appearance, MLB pitching one pitch. A pick short of it is VOID: it leaves the entry, which "
             "is paid as an entry of its remaining picks (a voided lineup is eligible for the guaranteed minimum "
             "payout of a lineup with that many picks, in Pick6 credits, less any cash it won). Read from what "
             "each box carries: NFL, Sleeper's snap counts (off_snp + def_snp + st_snp) where Sleeper has "
             "them, else any recorded stat (a stat needs a snap) -- Sleeper's gp and gms_active are shown as "
             "evidence, never read as a snap; NBA, ESPN's did-not-play flag, then minutes or any stat; MLB, "
             "plateAppearances for a batting stat, numberOfPitches for a pitching stat. Where the box cannot "
             "establish it (an active NBA line with 0 MIN and no stats could be seconds played; no snap count "
             "and no stat; a player not in the box) the pick goes to manual review with the evidence.",
     "source": _DK_PAGES + "; DraftKings Network, 2024-08-23 (the void payout)"},
    {"id": "special", "verified": True, "basis": _DK_CONFIRMED + " (the exceptions are not listed)",
     "text": "Certain specialized stat categories have exceptions to the standard requirement, and the ledger "
             "does not know which. A stat that can come without the standard participation -- MLB runs, stolen "
             "bases and H+R+RBI for a player in the box with no plate appearance (a pinch runner) -- goes to "
             "manual review when participation is borderline, never a guess.",
     "source": _DK_PAGES},
    {"id": "tie", "verified": True, "basis": _DK_CONFIRMED,
     "text": "A final stat exactly on the line is VOID, More or Less: the pick leaves the entry like a "
             "did-not-play. (Until the owner read the pages the ledger scored a tie as a loss, the "
             "conservative reading.)",
     "source": _DK_PAGES},
    {"id": "pardon", "verified": True, "basis": "stated by DraftKings, read by the owner 2026-11-27",
     "text": "The Pick6 Pardon VOIDS an unsuccessful MORE selection on a full-game player stat (never a First, "
             "Longest, Game High or other event-based market; regular season and postseason; live full-game "
             "picks too; no proof of injury needed) when the player exits early: NFL, an offensive player with "
             "a first-half snap and no snaps afterward (special-teams snaps count; defensive players and "
             "kickers excluded); NBA and WNBA, a player who plays in the first half and records no playing "
             "time afterward; MLB, a hitter with exactly one plate appearance (pitchers excluded); NHL, a "
             "skater who plays in the first period and records no ice time afterward (goalies excluded). A "
             "successful More pick and every Less pick keep their normal grade; a sport the policy does not "
             "list is not pardoned. The grader voids a pick only where the box ESTABLISHES the exit and grades a "
             "loss only where it establishes the pick does not qualify: MLB reads the plate appearances; the "
             "NFL reads Sleeper's first-half yards, which prove a second-half snap when the full-game yards "
             "differ but cannot prove none (Sleeper carries no snaps by half); the NBA reads ESPN's "
             "play-by-play, which proves time after halftime when it shows him on the floor but cannot prove "
             "none (it does not log who starts a period); NHL and WNBA have no grader here. Everything else "
             "goes to your review (loss or pardoned) with what is missing named.",
     "source": "pick6.draftkings.com/pardon-policy, read by the owner 2026-11-27"},
    {"id": "missing", "verified": True, "basis": "this ledger's rule",
     "text": "A player not found in the box score is never guessed: the pick goes to manual review (did not "
             "dress: mark it void; a different spelling: type his stat). Two players or two games matching one "
             "name (a doubleheader) go the same way.",
     "source": "this ledger's own rule"},
    {"id": "payout", "verified": True, "basis": "the owner's entry",
     "text": "The paper result is the schedule you typed from the entry: k correct of n live picks returns "
             "x times the stake (stake included). Pick6 pays from prize pools, so the figure is DraftKings', "
             "never ours. A count your rows describe pays only what they list; an all-or-nothing schedule "
             "with a live miss pays 0; a reduced entry (a void, a tie, a pardon) with no row for its live "
             "count waits for DraftKings' figure. An entry settles only when every pick is decided, and the "
             "record counts nothing still in review.",
     "source": "the owner's entry; RotoGrinders (peer-to-peer prize pools)"},
    {"id": "picks", "verified": True, "basis": "third-party guides",
     "text": "2 to 6 picks, at least two different players, each player and stat once.",
     "source": "FantasyLife and RotoGrinders' Pick6 guides"},
    {"id": "timing", "verified": True, "basis": "this ledger's rule",
     "text": "Graded from NFL: Sleeper's weekly box, the Tuesday after the game date (the week is over; regular "
             "season only -- a playoff pick is typed by hand); "
             "MLB: statsapi's final box scores, the day after; NBA: ESPN's final box scores, the day after. "
             "A stat correction after grading is not re-read: set it by hand.",
     "source": "the DFS look-back's, the prop recorder's and the Matchups engine's box sources"},
    {"id": "model", "verified": True, "basis": "this ledger's rule",
     "text": "Vigil's figure is read when you save an entry on or before its game date. NFL: the week sim's "
             "stat histograms give ANY half-point line exactly (the PC files them with its board build; "
             "passing, rushing, receiving yards, receptions, rush + rec and pass + rush yards); without them, "
             "the Pick 6 board's one line a player and stat. Baseball: every line the Pick 6 sheet's sim "
             "tracked. LoL: the Pick 6 board's line. A whole-number line, any other stat or sport, or a later "
             "entry says \"no model figure\" and is left out of the calibration.",
     "source": "this ledger's own rule"},
]


# ---- the tables ------------------------------------------------------------------
_ensured = [False]


def _ensure():
    if _ensured[0]:
        return
    with store._lock, store._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS pick6_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER NOT NULL,     -- the server's clock when the owner saved it
                sport TEXT NOT NULL,
                game_date TEXT NOT NULL,         -- the slate's date, as typed (YYYY-MM-DD)
                entry_type TEXT,                 -- DraftKings' label for the entry, as typed
                schedule TEXT NOT NULL,          -- JSON [[k, n, x]]: k of n live picks returns x * stake
                stake REAL NOT NULL,             -- paper dollars
                n_picks INTEGER NOT NULL,
                historical INTEGER DEFAULT 0,    -- saved after its game date: no Vigil figure
                status TEXT DEFAULT 'open',      -- open | awaiting_payout | settled
                n_live INTEGER, n_hit INTEGER,
                payout_x REAL, payout REAL, pnl REAL,
                credits INTEGER DEFAULT 0,       -- paid with a void pick: DraftKings pays that in credits
                settled_at INTEGER,
                why TEXT,                        -- what an unsettled entry waits for
                notes TEXT
            )""")
        c.execute("""
            CREATE TABLE IF NOT EXISTS pick6_picks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL,
                idx INTEGER NOT NULL,
                sport TEXT NOT NULL,             -- the entry's, so the record groups without a join
                player TEXT NOT NULL,
                stat TEXT NOT NULL,              -- a SPORTS stat key, or 'custom' (graded by hand)
                stat_label TEXT NOT NULL,
                line REAL NOT NULL,
                side TEXT NOT NULL,              -- More | Less
                p_model REAL,                    -- Vigil's P(this side wins) at entry; NULL = none
                model_src TEXT,                  -- the board it came from, or why there is none
                result TEXT DEFAULT 'pending',   -- pending | review | hit | miss | tie | void
                actual REAL,
                graded_by TEXT,                  -- sleeper | mlb | espn | owner
                graded_at INTEGER,
                note TEXT,                       -- why a grade waits, or what voided it
                won INTEGER,                     -- 1 / 0 where the pick counts, NULL where it does not
                ll REAL, brier REAL,             -- Vigil's scores on it, written at grade time
                review TEXT,                     -- what a 'review' pick waits on: participation | pardon
                evidence TEXT,                   -- JSON: the participation figures the box carried
                void_cause TEXT,                 -- a void's cause: dnp | tie | pardon | owner
                kind TEXT DEFAULT 'full'         -- full | event | part (KINDS): only 'full' can be pardoned
            )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_pick6_picks_entry ON pick6_picks(entry_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_pick6_picks_result ON pick6_picks(result)")
        cols = {r[1] for r in c.execute("PRAGMA table_info(pick6_picks)")}
        for col in ("review TEXT", "evidence TEXT", "void_cause TEXT",   # tables made before 2026-11-27's rules
                    "kind TEXT DEFAULT 'full'"):
            if col.split()[0] not in cols:
                try:
                    c.execute(f"ALTER TABLE pick6_picks ADD COLUMN {col}")
                except Exception as e:
                    errlog.note("P6L-migrate", e)       # a sibling worker added it first
    _ensured[0] = True


def _norm(name):
    """The player match key: kalshi_nfl._norm's rule (accents and Jr/Sr/II-V
    dropped) except that apostrophes and periods JOIN instead of splitting, so
    the name the owner types meets the box's spelling either way -- "JaMarr" and
    "Ja'Marr" are one key here, two under the split ("ja marr" vs "jamarr"), and
    "AJ Brown" meets "A.J. Brown". Both sides of every match go through this."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"['\u2019.`]", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", " ", s)
    s = re.sub(r"[^a-z ]", " ", s)
    return " ".join(s.split())


def _today_et():
    import clock
    return clock.today_et()


# ---- validation ------------------------------------------------------------------
_ROW = re.compile(r"^(\d{1,2})\s*(?:/\s*(\d{1,2}))?\s*=\s*(\d{1,5}(?:\.\d{1,4})?)\s*x?$", re.I)


def parse_schedule(text, n_picks):
    """'6/6=25, 5/6=2' (or '6=25', n defaulting to the entry's pick count) ->
    sorted [[k, n, x]]. Refuses a row the entry cannot reach, a duplicate, and a
    schedule with no payout for all n_picks correct."""
    if isinstance(text, (list, tuple)):
        toks = [f"{r[0]}/{r[1]}={r[2]}" for r in text]
    else:
        toks = [t.strip() for t in re.split(r"[,;\n]+", str(text or "")) if t.strip()]
    if not toks:
        raise ValueError("type the payout schedule DraftKings shows on the entry, e.g. 6/6=25")
    if len(toks) > 30:
        raise ValueError("at most 30 schedule rows")
    rows, seen = [], set()
    for t in toks:
        m = _ROW.match(t)
        if not m:
            raise ValueError(f"schedule row '{t[:20]}' is not k/n=x (k correct of n live picks returns x times the stake)")
        k = int(m.group(1))
        n = int(m.group(2)) if m.group(2) is not None else int(n_picks)
        x = float(m.group(3))
        if not (0 <= k <= n <= n_picks):
            raise ValueError(f"schedule row '{t[:20]}': need 0 <= correct <= live picks <= {n_picks}")
        if not (0 <= x <= 10000) or not math.isfinite(x):
            raise ValueError(f"schedule row '{t[:20]}': the multiplier must be 0 to 10,000")
        if (k, n) in seen:
            raise ValueError(f"schedule row {k}/{n} appears twice")
        seen.add((k, n))
        rows.append([k, n, round(x, 4)])
    rows.sort(key=lambda r: (-r[1], -r[0]))
    return rows


def _clean_text(v, cap, what, required=False):
    s = " ".join(str(v or "").split())
    if any(ord(ch) < 32 for ch in s):
        raise ValueError(f"{what} has a control character")
    if required and not s:
        raise ValueError(f"{what} is required")
    if len(s) > cap:
        raise ValueError(f"{what} is longer than {cap} characters")
    return s


def validate(d, today=None):
    """The owner's form -> a clean entry, or ValueError naming the first problem."""
    if not isinstance(d, dict):
        raise ValueError("send the entry as a JSON object")
    sport = str(d.get("sport") or "").strip().lower()
    if sport not in SPORTS:
        raise ValueError(f"sport must be one of {', '.join(SPORTS)}")
    try:
        gd = datetime.date.fromisoformat(str(d.get("game_date") or ""))
    except ValueError:
        raise ValueError("game date must be YYYY-MM-DD") from None
    today = today or _today_et()
    if gd > today + datetime.timedelta(days=14) or gd < today - datetime.timedelta(days=400):
        raise ValueError("game date must be within the last 400 days or the next 14")
    try:
        stake = float(d.get("stake"))
    except (TypeError, ValueError):
        raise ValueError("stake must be a number of (paper) dollars") from None
    if not (math.isfinite(stake) and 0 < stake <= 10000):
        raise ValueError("stake must be more than $0 and at most $10,000")
    picks_in = d.get("picks")
    if not isinstance(picks_in, list) or not (MIN_PICKS <= len(picks_in) <= MAX_PICKS):
        raise ValueError(f"a Pick6 entry has {MIN_PICKS} to {MAX_PICKS} picks")
    stats = SPORTS[sport]["stats"]
    picks, seen = [], set()
    for i, p in enumerate(picks_in, 1):
        if not isinstance(p, dict):
            raise ValueError(f"pick {i} is not an object")
        player = _clean_text(p.get("player"), 60, f"pick {i}'s player", required=True)
        stat = str(p.get("stat") or "").strip()
        if stat in stats:
            label = stats[stat][0]
        elif stat == "custom" or (not stats and stat):
            label = _clean_text(p.get("stat_label") or ("" if stat == "custom" else stat), 40,
                                f"pick {i}'s stat", required=True)
            stat = "custom"
        else:
            raise ValueError(f"pick {i}'s stat is not one of {sport.upper()}'s (or 'custom')")
        try:
            line = float(p.get("line"))
        except (TypeError, ValueError):
            raise ValueError(f"pick {i}'s line must be a number") from None
        if not (math.isfinite(line) and 0 <= line <= 100000) or abs(line * 100 - round(line * 100)) > 1e-6:
            raise ValueError(f"pick {i}'s line must be 0 to 100,000 with at most two decimals")
        side = str(p.get("side") or "").strip().capitalize()
        if side not in ("More", "Less"):
            raise ValueError(f"pick {i}'s side must be More or Less")
        key = (_norm(player), stat if stat != "custom" else "c:" + label.lower())
        if key in seen:
            raise ValueError(f"pick {i} repeats a player and stat already on the entry")
        seen.add(key)
        # every registry stat is a full-game one; a hand-typed stat names its kind (only full ones are pardoned)
        kind = "full" if stat != "custom" else str(p.get("kind") or "full").strip().lower()
        if kind not in KINDS:
            raise ValueError(f"pick {i}'s market kind must be one of {', '.join(KINDS)}")
        picks.append({"player": player, "stat": stat, "stat_label": label, "line": round(line, 2), "side": side,
                      "kind": kind})
    if len({_norm(p["player"]) for p in picks}) < 2:
        raise ValueError("a Pick6 entry needs at least two different players")
    rows = parse_schedule(d.get("schedule"), len(picks))
    n = len(picks)
    if not any(r[0] == n and r[1] == n and r[2] > 0 for r in rows):
        raise ValueError(f"the schedule needs DraftKings' payout for all {n} correct ({n}/{n}=x)")
    return {"sport": sport, "game_date": gd.isoformat(), "stake": round(stake, 2), "picks": picks,
            "schedule": rows,
            "entry_type": _clean_text(d.get("entry_type"), 40, "entry type"),
            "notes": _clean_text(d.get("notes"), 200, "notes"),
            "historical": 1 if gd < today else 0}


# ---- Vigil's figure at entry -------------------------------------------------------
def _side_prob(p_more_or_lean, lean_side, side):
    """A half-point line has no tie, so the other side is the complement."""
    return p_more_or_lean if side == lean_side else 1.0 - p_more_or_lean


def _nfl_prob(game_date, pick, now):
    """The NFL tab's Pick 6 view: nfl_dfs_sim's week board, one line a player and
    stat (its lean, 52-90%). Read off the shared board only -- never a build."""
    import boardshare
    import nfl_dfs_sim
    import nfl_game_sim
    labels = {"pass_yd": "pass yds", "rush_yd": "rush yds", "rec_yd": "rec yds", "rec": "receptions"}
    priced = set(labels) | set(nfl_dfs_sim.DIST_COMBOS)
    if pick["stat"] not in priced:
        return None, ("Vigil's NFL week sim prices passing, rushing and receiving yards, receptions and the two "
                      "yardage sums only")
    week = nfl_game_sim.current_week(day=game_date)
    season = nfl_dfs_sim._season()
    nm = _norm(pick["player"])
    # Every half-point line, exactly, off the week sim's stat histograms
    # (nfl_dfs_sim.stat_hist): the PC's board build files them as their own piece.
    dists, dage = boardshare.get(nfl_dfs_sim.dists_name(season, week), _MODEL_MAX_AGE_S)
    if dists and dists.get("dists"):
        m = pick["line"] - 0.5
        if abs(m - round(m)) > 1e-9:
            return None, ("Vigil's sim prices half-point lines only (its yards and catches are continuous, and a "
                          "real tie on a whole line is void)")
        hits = [d for name, d in dists["dists"].items() if _norm(name) == nm]
        if len(hits) == 1:
            h = hits[0].get(pick["stat"])
            if h is None:
                return None, f"Vigil's week-{week} sim gives him no {pick['stat_label'].lower()}"
            p_more = nfl_dfs_sim.hist_over(h, dists["n"], pick["line"])
            return (_side_prob(p_more, "More", pick["side"]),
                    f"NFL week sim, week {week}, any line ({dists['n']:,} worlds, {int(dage // 60)} min old)")
    if pick["stat"] not in labels:
        return None, f"no NFL week-{week} stat histograms built in the last 6 h for this player (the PC builds them)"
    board, age = boardshare.get(f"nfl_sim_board_{season}_w{week}_0", _MODEL_MAX_AGE_S)
    if board is None:
        hit = nfl_dfs_sim._cache.get(("nfl_sim_board", season, week, False))
        if hit and now - hit[0] <= _MODEL_MAX_AGE_S:
            board, age = hit[1], now - hit[0]
    if not board or not board.get("props"):
        return None, f"no NFL Pick 6 board for week {week} built in the last 6 h (open the Pick 6 view first)"
    lab = labels[pick["stat"]]
    rows = [p for p in board["props"] if _norm(p.get("player")) == nm and p.get("stat") == lab]
    if not rows:
        return None, f"Vigil's week-{week} board has no {lab} lean for this player"
    r = rows[0]
    if abs(float(r["line"]) - pick["line"]) > 1e-9:
        return None, f"Vigil's board prices this at {r['line']} only"
    return (_side_prob(float(r["prob"]) / 100.0, r["side"], pick["side"]),
            f"NFL Pick 6 board, week {week}, {int(age // 60)} min old")


def _mlb_prob(game_date, pick, now):
    """The baseball Pick 6 sheet: every line the shared game sim tracked, read
    from a CACHED slate and CACHED sims only (the season switch and the web
    worker's memory both forbid a build here)."""
    import baseball
    tmap = {"hit": "hit", "tb": "tb", "hrr": "hrr", "hr": "hr", "sb": "sb", "p_k": "ks"}
    if pick["stat"] not in tmap:
        return None, "Vigil's baseball sim does not price this stat"
    games = baseball.analyze_slate(game_date, game_date[:4], cached_only=True)
    if not games:
        return None, "no baseball slate cached for that date"
    nm, t = _norm(pick["player"]), tmap[pick["stat"]]
    N = pick["line"] + 0.5
    if abs(N - round(N)) > 1e-9:
        return None, "Vigil's sim prices half-point lines only"
    for g in games:
        pk = g.get("game_pk")
        hit = baseball._cache.get(("game_sim", pk))
        gs = hit[1] if hit and len(hit) > 2 and now - hit[0] < hit[2] else baseball._sim_disk_get(pk)
        for c in (gs or {}).get("cands") or []:
            kref = c.get("kref") or {}
            if (c.get("side", "yes") == "yes" and kref.get("t") == t and kref.get("line") == int(round(N))
                    and _norm(kref.get("player")) == nm):
                return _side_prob(float(c["marg"]), "More", pick["side"]), f"baseball Pick 6 sheet, {g.get('matchup')}"
    return None, "no cached baseball sim has this player at this line"


def _lol_prob(game_date, pick, now):
    """LoL's Pick 6 board (lol.board), read from the shared copy only."""
    import boardshare
    labels = {"kills": "kills", "assists": "assists", "cs": "CS"}
    if pick["stat"] not in labels:
        return None, "Vigil's LoL board prices kills, assists and CS only"
    board, age = boardshare.get("lol_6", _MODEL_MAX_AGE_S)
    if not board or not board.get("picks"):
        return None, "no LoL Pick 6 board built in the last 6 h"
    nm = _norm(pick["player"])
    for r in board["picks"]:
        if _norm(r.get("player")) == nm and r.get("stat") == labels[pick["stat"]]:
            if abs(float(r["line"]) - pick["line"]) > 1e-9:
                return None, f"Vigil's board prices this at {r['line']} only"
            return _side_prob(float(r["prob"]) / 100.0, r["side"], pick["side"]), f"LoL Pick 6 board, {int(age // 60)} min old"
    return None, "Vigil's LoL board has no lean for this player and stat"


_PRICERS = {"nfl": _nfl_prob, "mlb": _mlb_prob, "lol": _lol_prob}


def model_prob(sport, game_date, pick, historical=False, now=None):
    """(Vigil's P(this side wins), where it came from) or (None, why none)."""
    if historical:
        return None, "saved after its game date: no pre-game figure"
    if pick.get("stat") == "custom":
        return None, "a stat Vigil does not model"
    fn = _PRICERS.get(sport)
    if fn is None:
        return None, f"Vigil has no {SPORTS[sport]['label']} Pick 6 model"
    try:
        p, src = fn(game_date, pick, now or time.time())
    except Exception as e:
        errlog.note("P6L-model", e, path=f"{sport} {pick.get('stat')}")
        return None, "Vigil's board could not be read"
    if p is None:
        return None, src
    return round(min(max(float(p), 0.0), 1.0), 4), src


# ---- grading ------------------------------------------------------------------------
def outcome(actual, line, side):
    """hit | miss | tie for a stat against a line."""
    if abs(float(actual) - float(line)) < 1e-9:
        return "tie"
    over = float(actual) > float(line)
    return "hit" if over == (side == "More") else "miss"


def scores(result, p):
    """(won, log loss, Brier) a graded pick adds to the record. A void, a pick
    still pending or in review never counts; a tie counts nowhere under
    TIE_RULE 'void' (DraftKings') and as a loss under 'loss'. Vigil's scores
    exist only where it gave a figure."""
    if result == "tie" and TIE_RULE == "void":
        return None, None, None
    if result not in ("hit", "miss", "tie"):
        return None, None, None
    won = 1 if result == "hit" else 0
    if p is None:
        return won, None, None
    q = min(max(float(p), 1e-4), 1 - 1e-4)
    ll = -math.log(q if won else 1.0 - q)
    return won, round(ll, 6), round((float(p) - won) ** 2, 6)


def settle(n_picks, results, rows):
    """(status, n_live, n_hit, payout_x, credits, why) for an entry's picks.
    `rows` are the schedule's [k, n, x]. A pick pending or in review anywhere:
    open -- an entry settles only when every pick is decided. Otherwise k
    correct of n live picks (a void and a tie leave under TIE_RULE 'void'; a
    tie stays live as a miss under 'loss'): the typed row pays it; at the full
    count, or at a count the rows describe, an unlisted k pays 0; an
    all-or-nothing schedule with a live miss pays 0; a reduced count with
    nothing typed waits for DraftKings' figure."""
    n_rev = sum(1 for r in results if r == "review")
    if n_rev:
        return "open", None, None, None, 0, f"{n_rev} pick{'s' if n_rev != 1 else ''} waiting for your review"
    if any(r == "pending" for r in results):
        return "open", None, None, None, 0, "picks still to grade"
    voids = ("void", "tie") if TIE_RULE == "void" else ("void",)
    live = [r for r in results if r not in voids]
    n, k = len(live), sum(1 for r in live if r == "hit")
    table = {(int(r[0]), int(r[1])): float(r[2]) for r in rows}
    reduced = n < n_picks
    if (k, n) in table:
        x = table[(k, n)]
    elif not reduced or any(r[1] == n for r in rows):
        x = 0.0
    elif k < n and all(int(r[0]) == int(r[1]) for r in rows):
        x = 0.0
    else:
        return ("awaiting_payout", n, k, None, 0,
                f"a void left {n} live pick{'s' if n != 1 else ''} with {k} correct: type DraftKings' payout "
                f"for {k}/{n}")
    return "settled", n, k, x, 1 if (reduced and x > 0) else 0, None


def _settle_entry(c, entry_id, now):
    e = c.execute("SELECT n_picks, schedule, stake FROM pick6_entries WHERE id=?", (entry_id,)).fetchone()
    if not e:
        return
    results = [r["result"] for r in c.execute(
        "SELECT result FROM pick6_picks WHERE entry_id=? ORDER BY idx", (entry_id,))]
    st, n, k, x, credits, why = settle(e["n_picks"], results, json.loads(e["schedule"]))
    if st == "settled":
        pay = round(e["stake"] * x, 2)
        c.execute("""UPDATE pick6_entries SET status=?, n_live=?, n_hit=?, payout_x=?, payout=?, pnl=?,
                     credits=?, settled_at=?, why=NULL WHERE id=?""",
                  (st, n, k, x, pay, round(pay - e["stake"], 2), credits, int(now), entry_id))
    else:
        c.execute("""UPDATE pick6_entries SET status=?, n_live=?, n_hit=?, payout_x=NULL, payout=NULL,
                     pnl=NULL, credits=0, settled_at=NULL, why=? WHERE id=?""", (st, n, k, why, entry_id))


def _write_grade(c, pick_id, result, actual, by, note, p, now, review=None, evidence=None, cause=None):
    """One pick's grade. `cause` says why a void is one -- dnp (short of the
    minimum participation), pardon, owner (voided by hand, no cause given); a
    tie's is 'tie' -- so the record can count voids by cause."""
    won, ll, br = scores(result, p)
    done = result not in ("pending", "review")
    cause = "tie" if result == "tie" else (cause or "owner") if result == "void" else None
    c.execute("""UPDATE pick6_picks SET result=?, actual=?, graded_by=?, graded_at=?, note=?, won=?, ll=?, brier=?,
                 review=?, evidence=?, void_cause=? WHERE id=?""",
              (result, actual, by if result != "pending" else None, int(now) if done else None,
               note, won, ll, br, review if result == "review" else None,
               json.dumps(evidence) if evidence else None, cause, pick_id))


def _ev_text(ev):
    """The participation evidence as the card prints it ("snaps 0 (off 0, st 0), gp 1")."""
    if not ev:
        return "no participation figures"
    parts = []
    for k, v in ev.items():
        if v is None or k == "dnp" and not v:
            continue
        if isinstance(v, bool):
            parts.append(k.replace("_", " ") if v else f"no {k.replace('_', ' ')}")
        elif isinstance(v, str):
            parts.append(f"{k.replace('_', ' ')} {v}")
        elif isinstance(v, (list, tuple)):
            parts.append(f"{k} {', '.join(str(x) for x in v)}" if v else f"no {k}")
        else:
            parts.append(f"{k} {int(v) if float(v).is_integer() else v}")
    return ", ".join(parts) or "no participation figures"


def pardon_check(sport, block, kind, ev):
    """(verdict, why) for a More pick that LOST: 'pardon' (the box establishes
    the qualifying exit: void), 'loss' (it establishes the pick does not
    qualify), 'review' (it cannot tell -- `why` names what is missing). Only
    what the data establishes decides (the owner, 2026-11-27: "mark the result
    for manual review rather than inventing an outcome")."""
    if kind != "full":
        return "loss", f"no pardon: a {KINDS.get(kind, kind).split(' (')[0].lower()} market is not a full-game stat"
    rule = PARDON.get(sport)
    if rule is None:
        return "loss", f"no pardon: DraftKings' Pardon Policy lists no {SPORTS[sport]['label']} rule"
    ev = ev or {}
    if sport == "mlb":
        if block not in rule["blocks"]:
            return "loss", "no pardon: pitchers are excluded"
        pa = ev.get("pa")
        if pa is None:
            return "review", "pardon not checked: the box carries no plate-appearance count"
        if pa == 1:
            return "pardon", "pardoned: a hitter with exactly one plate appearance (DraftKings' Pardon)"
        return "loss", f"no pardon: {int(pa)} plate appearances (the Pardon needs exactly one)"
    if sport == "nfl":
        pos = ev.get("pos")
        if pos and pos not in rule["eligible"]:
            return "loss", f"no pardon: {pos} -- {rule['excluded']} are excluded"
        if ev.get("second_half"):
            return "loss", (f"no pardon: second-half yards in Sleeper's half split ({ev['second_half']}) -- "
                            "he had a snap after halftime")
        if ev.get("half_split") is False:
            return "review", ("pardon not checked: Sleeper's weekly stats carry no half split for him, so a "
                              "first-half snap with none afterward cannot be established (snaps by half need "
                              "ESPN's play-by-play, which no grader here reads per snap)")
        return "review", ("pardon not checked: Sleeper's half split shows no second-half yards, but a snap "
                          "without a touch leaves no trace in it -- whether he had a snap after halftime is not "
                          "established")
    if sport == "nba":
        if ev.get("second_half"):
            return "loss", f"no pardon: on the floor after halftime in ESPN's play-by-play ({ev['second_half']})"
        if ev.get("first_half") is None:
            return "review", "pardon not checked: no ESPN play-by-play for this game"
        # Neither absence proves anything: ESPN does not log who starts a period,
        # so a player can be on the floor with no event in a stretch of a half.
        return "review", (("pardon not checked: first-half participation established, " if ev.get("first_half")
                           else "pardon not checked: no first-half event either, ")
                          + "no second-half event -- ESPN's play-by-play does not log who starts a period, so "
                            "'no playing time afterward' is not established")
    return "review", (f"pardon not checked: no {SPORTS[sport]['label']} grader reads "
                      + ("ice time by period" if sport == "nhl" else "playing time by half"))


def decide(sport, block, line, side, actual, status, ev, special=False, kind="full"):
    """(result, actual, note, review, cause) for a pick whose box says
    `status` (met | not_met | borderline | unknown) about the player's
    participation, with `ev` the figures it carried. Not met: VOID (DraftKings'
    minimum participation). Unknown -- or borderline on a `special` stat, which
    may have an unlisted exception -- goes to manual review, never a guess;
    borderline on a standard stat is not met. Met: graded on the stat, a tie
    void; a More pick that would LOSE meets the Pardon (pardon_check), which
    voids it, grades it a loss or sends it to review."""
    evs = _ev_text(ev)
    if status == "borderline":
        status = "unknown" if special else "not_met"
    if status == "not_met":
        return "void", None, f"short of DraftKings' minimum participation ({evs}): void", None, "dnp"
    if status != "met":
        return ("review", actual, f"participation not established ({evs}): decide whether he met DraftKings' "
                                  "minimum", "participation", None)
    res = outcome(actual, line, side)
    if res != "miss" or side != "More":
        return res, actual, None, None, None
    verdict, why = pardon_check(sport, block, kind, ev)
    if verdict == "pardon":
        return "void", actual, why, None, "pardon"
    if verdict == "loss":
        return "miss", actual, why, None, None
    return "review", actual, f"{why} ({evs}): mark it a loss or pardoned", "pardon", None


# The box parsers: each returns {norm name: [row]} where a row is
# {"part": {block: (status, evidence)}, "stats": {block: {field: value}}} --
# status met | not_met | borderline | unknown against DraftKings' minimum
# participation (decide reads it), evidence the figures that decided it, shown
# on the card. A list, so a name that two players (or a doubleheader) share is
# seen, never guessed.

# Sleeper keys that are NOT evidence of being on the field: the games flags,
# team totals, ranks, fantasy points and bonuses (each follows from a stat, or
# from nothing at all -- an inactive row carries gms_active and its ranks).
_NOT_ON_FIELD = ("gp", "gs", "gms_active")
_NOT_ON_FIELD_PREFIX = ("tm_", "pos_rank", "pts_", "bonus_", "rank")
_SNAPS = ("off_snp", "def_snp", "st_snp")


_HALF = (("rush_yd", "h1_rush_yd"), ("rec_yd", "h1_rec_yd"), ("pass_yd", "h1_pass_yd"))


def nfl_half(st):
    """(half_split, second_half) off Sleeper's first-half yardage keys: whether
    the row carries the split at all (any h1_ key; Sleeper omits a zero, so a
    missing h1_x beside one present is 0), and where the full-game yards differ
    from the first half's, the second-half yards that prove a snap after
    halftime ("rush +12"). A second-half snap with no net yards leaves no trace,
    so equality proves nothing either way."""
    if not any(k.startswith("h1_") for k in st):
        return False, None
    moved = [f"{k.split('_')[0]} {float(st.get(k) or 0) - float(st.get(h) or 0):+g}" for k, h in _HALF
             if abs(float(st.get(k) or 0) - float(st.get(h) or 0)) > 1e-9]
    return True, (", ".join(moved) or None)


def nfl_participation(st, pos=None):
    """(status, evidence) for one Sleeper weekly row: one snap is DraftKings'
    requirement. Snap counts decide where Sleeper carries them (on 2026 week
    12, 12 of 95 QB/WR rows did). Without them any recorded stat is a snap (a
    target, a carry, an attempt, a return, a penalty all need one). gp and
    gms_active are shown, never read as a snap: an active row with gp 0 and no
    stat -- seventeen such running-back rows that week -- could be a special-
    teams snap Sleeper does not count, so it is unknown. The evidence also
    carries what the Pardon reads: the position and the half split (nfl_half)."""
    ev = {"pos": pos or None, "snaps": None}
    ev["half_split"], ev["second_half"] = nfl_half(st)
    snaps = {k: float(st[k] or 0) for k in _SNAPS if k in st}
    if snaps:
        ev["snaps"] = sum(snaps.values())
        ev.update(snaps)
    ev["gp"] = float(st.get("gp") or 0)
    if "gms_active" in st:
        ev["gms_active"] = float(st.get("gms_active") or 0)
    if snaps:
        return ("met" if ev["snaps"] >= 1 else "not_met"), ev
    acted = sorted(k for k, v in st.items() if isinstance(v, (int, float)) and v and k not in _NOT_ON_FIELD
                   and k not in _SNAPS and not k.startswith(_NOT_ON_FIELD_PREFIX))
    if acted:
        ev["recorded"] = acted[:3]
        return "met", ev
    ev["recorded"] = []
    return "unknown", ev


def parse_sleeper(rows):
    """Sleeper's weekly stats rows (the endpoint dfslog.actuals_nfl reads)."""
    out = {}
    for r in rows or []:
        pl = r.get("player") or {}
        st = r.get("stats") or {}
        nm = _norm(f"{pl.get('first_name', '')} {pl.get('last_name', '')}")
        if not nm or pl.get("position") == "DEF":
            continue
        out.setdefault(nm, []).append({"part": {"": nfl_participation(st, pl.get("position"))},
                                       "stats": {"": {k: float(v or 0) for k, v in st.items()
                                                      if isinstance(v, (int, float))}}})
    return out


def mlb_participation(bat, pit):
    """{block: (status, evidence)}: batting needs one plate appearance, pitching
    one pitch (DraftKings). An empty block is a player who never appeared there
    (bench, or a position player who did not pitch): not met. A batting block
    with no plate appearance (a pinch runner, a defensive replacement) is
    BORDERLINE -- not met for a standard stat, review for a `special` one. A
    block without the count (an older feed) is met only on an at-bat / an out
    or a batter faced, else unknown."""
    f = lambda b, k: None if b.get(k) is None else float(b.get(k) or 0)  # noqa: E731
    if not bat:
        b = ("not_met", {"pa": 0, "appeared": False})
    else:
        pa, ab = f(bat, "plateAppearances"), f(bat, "atBats")
        ev = {"pa": pa, "ab": ab}
        if pa is not None:
            b = ("met" if pa >= 1 else "borderline", ev)
        else:
            b = ("met" if (ab or 0) >= 1 else "unknown", ev)
    if not pit:
        p = ("not_met", {"pitches": 0, "appeared": False})
    else:
        n = f(pit, "numberOfPitches")
        if n is None:
            n = f(pit, "pitchesThrown")
        ev = {"pitches": n, "outs": f(pit, "outs"), "bf": f(pit, "battersFaced")}
        if n is not None:
            p = ("met" if n >= 1 else "not_met", ev)
        else:
            p = ("met" if (ev["outs"] or 0) + (ev["bf"] or 0) >= 1 else "unknown", ev)
    return {"bat": b, "pit": p}


def parse_mlb_box(box, out=None):
    """One statsapi box score: every player on both rosters, with his
    participation in each block (mlb_participation)."""
    out = {} if out is None else out
    for side in ("home", "away"):
        for pl in ((box.get("teams") or {}).get(side, {}).get("players") or {}).values():
            nm = _norm((pl.get("person") or {}).get("fullName"))
            if not nm:
                continue
            st = pl.get("stats") or {}
            bat, pit = st.get("batting") or {}, st.get("pitching") or {}
            num = lambda b: {k: float(v) for k, v in b.items() if isinstance(v, (int, float))}  # noqa: E731
            out.setdefault(nm, []).append({"part": mlb_participation(bat, pit),
                                           "stats": {"bat": num(bat), "pit": num(pit)}})
    return out


def nba_participation(r, dnp, halves=None):
    """(status, evidence) for one ESPN box row: one second of official playing
    time (DraftKings). ESPN's did-not-play flag is not met; a minute or any
    stat is met; an ACTIVE line with 0 MIN and no stats could be seconds on the
    floor (ESPN rounds the minutes), so it is unknown -- manual review. The
    evidence also carries what the Pardon reads (nba_halves): first-half
    participation, and where the play-by-play shows it, time after halftime."""
    any_stat = any(r.get(k) for k in ("pts", "fga", "fta", "reb", "ast", "stl", "blk", "tov", "pf"))
    ev = {"minutes": float(r.get("min") or 0), "dnp": bool(dnp), "any_stat": bool(any_stat)}
    if halves is not None:
        first, second = halves
        ev["first_half"] = bool(r.get("starter")) or r.get("pid") in first
        ev["second_half"] = second.get(r.get("pid"))
    else:
        ev["first_half"] = None
    if dnp:
        return "not_met", ev
    if ev["minutes"] >= 1 or any_stat:
        return "met", ev
    return "unknown", ev


def _per_label(per, clk):
    return f"{'Q%d' % per if per <= 4 else 'OT%d' % (per - 4)} {clk // 60}:{clk % 60:02d}"


def nba_halves(d):
    """(first-half players, {player: where he was on the floor after halftime})
    off ESPN's play-by-play, through nba_pbp's own parse. On the floor means a
    play that shows him there (nba_matchup_data._actors: technicals and
    ejections name the bench and are not evidence) or a substitution with time
    on both sides of it -- an entry with clock left, an exit after the period's
    start, never an in-and-out at one dead ball (a free-throw swap plays no
    time). ESPN does not log who starts a period, so this PROVES time after
    halftime when it finds it and proves nothing when it does not. None
    without play-by-play."""
    import nba_matchup_data as M
    import nba_pbp as P
    rec = P.parse_summary(d) if d.get("plays") else None
    if not rec:
        return None
    a, W = rec["plays"], P.PLAY_W
    first, second, subs = set(), {}, {}

    def seen(pid, per, clk):
        if per <= 2:
            first.add(pid)
        elif pid not in second:
            second[pid] = _per_label(per, clk)
    for j in range(0, len(a), W):
        p = a[j:j + W]
        per, clk = p[P.I_PER], p[P.I_CLK]
        if p[P.I_CODE] == P.SUB:
            for pid, how in ((p[P.I_P1], "in"), (p[P.I_P2], "out")):
                if pid:
                    subs.setdefault(pid, set()).add((per, clk, how))
            continue
        for pid in M._actors(p):
            seen(pid, per, clk)
    for pid, stamps in subs.items():
        for per, clk, how in sorted(stamps):
            if (per, clk, "out" if how == "in" else "in") in stamps:
                continue
            if (how == "in" and clk > 0) or (how == "out" and clk < M._period_len(per)):
                seen(pid, per, clk)
    return first, second


def parse_nba_summary(d, out=None):
    """One ESPN NBA summary, through the Matchups engine's own box parser, with
    ESPN's did-not-play flag read beside it (parse_box folds it into `played`)
    and the halves the Pardon reads (nba_halves)."""
    import nba_matchup_data
    out = {} if out is None else out
    teams = (d.get("boxscore") or {}).get("players") or []
    sides = {str((t.get("team") or {}).get("id")): i for i, t in enumerate(teams)}
    dnp = {}
    for t in teams:
        for st in t.get("statistics") or []:
            for a in st.get("athletes") or []:
                dnp[str((a.get("athlete") or {}).get("id"))] = bool(a.get("didNotPlay"))
    try:
        halves = nba_halves(d)
    except Exception as e:                  # the box still grades; the Pardon then waits for review
        errlog.note("P6L-nba-halves", e)
        halves = None
    for r in nba_matchup_data.parse_box(d, sides):
        nm = _norm(r.get("name"))
        if nm:
            out.setdefault(nm, []).append({"part": {"": nba_participation(r, dnp.get(str(r.get("pid")), False),
                                                                           halves)},
                                           "stats": {"": {k: float(r[k]) for k in ("pts", "reb", "ast", "tpm",
                                                                                     "stl", "blk", "tov")}}})
    return out


def _first_tuesday_after(d):
    days = (1 - d.weekday()) % 7
    return d + datetime.timedelta(days=days or 7)


def ready(sport, game_date, today):
    """Is the box final? Football the Tuesday after (Monday night is in the
    week), baseball and basketball the next day."""
    d = datetime.date.fromisoformat(game_date)
    if sport == "nfl":
        return today >= _first_tuesday_after(d)
    return today > d


def _fetch_box(sport, game_date, budget):
    """The box for a sport's date, off the app's existing sources, or None when
    it is not final or the pass is out of calls. `budget` is [calls left]."""
    def spend(n=1):
        if budget[0] < n:
            return False
        budget[0] -= n
        return True

    if sport == "nfl":
        import dfslog
        import nfl_game_sim
        import nfl_live
        import racing
        if not spend(2):
            return None
        # current_week answers 18 for any date past the regular season, and the
        # weekly feed below is the REGULAR season's: a playoff pick read that way
        # would be graded on his week-18 line. So the date must hold a game of
        # the week it maps to, or the pick waits for the owner.
        season = int(nfl_game_sim._season())
        week = nfl_game_sim.current_week(day=game_date)
        sch = nfl_live.schedule(week, season, seasontype=2) or []
        if not any(nfl_game_sim._et_day(g.get("date")) == game_date for g in sch):
            return {"_why": f"no regular-season NFL game on {game_date} in ESPN's schedule (the weekly box this "
                            "reads is the regular season's): type his final stat"}
        url = dfslog._SLEEPER_STATS.format(season=season, week=week)
        for pos in ("QB", "RB", "WR", "TE"):
            url += f"&position[]={pos}"
        rows = racing._get_json(url + "&order_by=pts_ppr")
        return parse_sleeper(rows) if rows else None
    if sport == "mlb":
        import baseball
        import kalshi
        if not spend(1):
            return None
        pks = sorted(baseball._final_winners(game_date).keys())
        if not pks or not spend(len(pks)):
            return None
        out = {}
        for pk in pks:
            try:
                parse_mlb_box(kalshi._get_json(f"{baseball.STATS_BASE}/game/{pk}/boxscore"), out)
            except Exception as e:
                errlog.note("P6L-mlb-box", e, path=str(pk))
                return None          # a partial date could hide a player: try the whole date again later
        return out
    if sport == "nba":
        import nba_pbp
        if not spend(1):
            return None
        evs = [e for e in nba_pbp._day_events(game_date.replace("-", "")) if e.get("state") == "post"]
        if not evs or not spend(len(evs)):
            return None
        out = {}
        for e in evs:
            try:
                parse_nba_summary(nba_pbp._get(f"{nba_pbp._SITE}/summary?event={e['id']}") or {}, out)
            except Exception as ex:
                errlog.note("P6L-nba-box", ex, path=str(e.get("id")))
                return None
        return out
    return None


def grade_pick(box, pick, source):
    """(result, actual, note, review, evidence, cause) for one pick against a
    final box. 'review' wherever the box cannot settle it without a guess: a
    name not in the box, two matches, participation not established, a More
    pick that lost whose Pardon the data cannot decide (decide). 'pending' only
    for a hand-graded stat."""
    stat = SPORTS[pick["sport"]]["stats"].get(pick["stat"])
    if stat is None:
        return "pending", None, "graded by hand: type his final stat", None, None, None
    block, fields, special = stat[1], stat[2], bool(stat[3]) if len(stat) > 3 else False
    rows = box.get(_norm(pick["player"])) or []
    if not rows:
        return ("review", None, f"not in {source}: participation not established -- if he did not play mark it "
                                "void; if the name differs, type his stat", "participation", None, None)
    if len(rows) > 1:
        return ("review", None, f"{len(rows)} matches for this name in {source} (two players, or a doubleheader): "
                                "type his stat or mark it void", "participation", None, None)
    r = rows[0]
    status, ev = r["part"][block]
    actual = sum(float(r["stats"].get(block, {}).get(f) or 0) for f in fields)
    res, actual, note, review, cause = decide(pick["sport"], block, pick["line"], pick["side"], actual, status,
                                              ev, special=special, kind=pick.get("kind") or "full")
    return res, actual, note, review, ev, cause


def grade_due(now=None, today=None, boxes=None, max_fetch=_MAX_FETCH):
    """Grade every pending auto-gradable pick whose box is final. `boxes` (tests)
    is a callable (sport, date, budget) -> box; the default reads the app's
    sources. Returns how many picks it graded."""
    _ensure()
    now = now or time.time()
    today = today or _today_et()
    boxes = boxes or _fetch_box
    auto = [s for s, v in SPORTS.items() if v["grader"]]
    with store._lock, store._conn() as c:
        due = [dict(r) for r in c.execute(
            f"""SELECT p.id, p.entry_id, p.sport, p.player, p.stat, p.line, p.side, p.p_model, p.kind, e.game_date
                FROM pick6_picks p JOIN pick6_entries e ON e.id = p.entry_id
                WHERE p.result = 'pending' AND p.stat != 'custom' AND p.sport IN ({','.join('?' * len(auto))})
                ORDER BY e.game_date LIMIT 500""", auto)]
    groups = {}
    for p in due:
        if ready(p["sport"], p["game_date"], today):
            groups.setdefault((p["sport"], p["game_date"]), []).append(p)
    budget, n, touched = [max_fetch], 0, set()
    for (sport, gd), picks in groups.items():
        try:
            box = boxes(sport, gd, budget)
        except Exception as e:
            errlog.note("P6L-box", e, path=f"{sport} {gd}")
            box = None
        if box is None:
            continue
        src = f"{_SOURCE[SPORTS[sport]['grader']]} ({gd})"
        with store._lock, store._conn() as c:
            for p in picks:
                # a box that cannot grade this date at all says why (`_why`)
                res, actual, note, review, ev, cause = (("pending", None, box["_why"], None, None, None)
                                                        if "_why" in box else grade_pick(box, p, src))
                _write_grade(c, p["id"], res, actual, SPORTS[sport]["grader"] if res != "pending" else None,
                             note, p["p_model"], now, review=review, evidence=ev, cause=cause)
                n += res not in ("pending", "review")
                touched.add(p["entry_id"])
            for eid in touched:
                _settle_entry(c, eid, now)
        touched.clear()
    return n


def tick():
    """The background owner's cadence (mlb_recorder's loop): one pass an hour."""
    now = time.time()
    if now - _state["last_grade"] < _GRADE_EVERY_S:
        return 0
    _state["last_grade"] = now
    if not _grade_lock.acquire(blocking=False):
        return 0
    try:
        return grade_due(now=now)
    finally:
        _grade_lock.release()


def grade_now():
    """The card's Grade button: one pass now, unless one is already running."""
    if not _grade_lock.acquire(blocking=False):
        return None
    try:
        return grade_due()
    finally:
        _grade_lock.release()


# ---- the owner's writes ----------------------------------------------------------------
def add_entry(d, now=None, today=None, pricer=None):
    """Validate, price each pick off Vigil's board, store. Returns the entry."""
    _ensure()
    now = now or time.time()
    e = validate(d, today=today)
    pricer = pricer or model_prob
    for p in e["picks"]:
        p["p_model"], p["model_src"] = pricer(e["sport"], e["game_date"], p, historical=bool(e["historical"]), now=now)
    with store._lock, store._conn() as c:
        cur = c.execute("""INSERT INTO pick6_entries (created_at, sport, game_date, entry_type, schedule, stake,
                           n_picks, historical, status, notes) VALUES (?,?,?,?,?,?,?,?, 'open', ?)""",
                        (int(now), e["sport"], e["game_date"], e["entry_type"], json.dumps(e["schedule"]),
                         e["stake"], len(e["picks"]), e["historical"], e["notes"]))
        eid = cur.lastrowid
        for i, p in enumerate(e["picks"]):
            c.execute("""INSERT INTO pick6_picks (entry_id, idx, sport, player, stat, stat_label, line, side,
                         p_model, model_src, kind, result, note) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'pending', ?)""",
                      (eid, i, e["sport"], p["player"], p["stat"], p["stat_label"], p["line"], p["side"],
                       p["p_model"], p["model_src"], p["kind"],
                       None if SPORTS[e["sport"]]["grader"] and p["stat"] != "custom"
                       else "graded by hand: type his final stat"))
    return entry(eid)


# The owner's answers to a pick in review (the card's two buttons a kind).
DECISIONS = {"participation": ("met", "not_met"), "pardon": ("loss", "pardoned")}


def set_pick(pick_id, actual=None, void=False, reset=False, decision=None, now=None):
    """The owner's grade: his typed final stat, a void, back to pending (the
    auto-grader then reads it again), or his answer to a review -- "met" (he
    played: grade the stat the box carried, the Pardon still applying) /
    "not_met" (void); "loss" / "pardoned" (a void) for the Pardon. A typed stat
    passes through the same rules: a tie is void, a More miss meets the
    Pardon. Returns the entry, or None."""
    _ensure()
    now = now or time.time()
    with store._lock, store._conn() as c:
        p = c.execute("SELECT * FROM pick6_picks WHERE id=?", (pick_id,)).fetchone()
        if not p:
            return None
        ev = json.loads(p["evidence"]) if p["evidence"] else None
        blk = (SPORTS[p["sport"]]["stats"].get(p["stat"]) or ("", ""))[1] or ""
        kind = p["kind"] or "full"
        if reset:
            _write_grade(c, pick_id, "pending", None, None, None, p["p_model"], now)
        elif void:
            _write_grade(c, pick_id, "void", None, "owner", "set void by hand", p["p_model"], now, evidence=ev,
                         cause="owner")
        elif decision is not None:
            if p["result"] != "review" or decision not in DECISIONS.get(p["review"] or "", ()):
                raise ValueError(f"'{decision}' does not answer this pick (it is {p['result']}"
                                 f"{', in ' + p['review'] + ' review' if p['review'] else ''})")
            if decision == "not_met":
                _write_grade(c, pick_id, "void", None, "owner", "participation not met (owner's review): void",
                             p["p_model"], now, evidence=ev, cause="dnp")
            elif decision == "met":
                if p["actual"] is None:
                    raise ValueError("this box has no stat for him: type his final stat instead")
                res, a, note, review, cause = decide(p["sport"], blk, p["line"], p["side"], p["actual"], "met",
                                                     ev, kind=kind)
                _write_grade(c, pick_id, res, a, "owner", note or "participation met (owner's review)",
                             p["p_model"], now, review=review, evidence=ev, cause=cause)
            elif decision == "loss":
                _write_grade(c, pick_id, "miss", p["actual"], "owner", "no pardon (owner's review): a loss",
                             p["p_model"], now, evidence=ev)
            else:
                _write_grade(c, pick_id, "void", p["actual"], "owner",
                             "pardoned (owner's review): voided, as DraftKings voids a qualifying selection",
                             p["p_model"], now, evidence=ev, cause="pardon")
        else:
            try:
                a = float(actual)
            except (TypeError, ValueError):
                raise ValueError("the final stat must be a number") from None
            if not (math.isfinite(a) and -1000 <= a <= 100000):
                raise ValueError("the final stat must be -1,000 to 100,000")
            # typing a stat says he played; the tie and the Pardon still apply
            res, a, note, review, cause = decide(p["sport"], blk, p["line"], p["side"], a, "met", ev, kind=kind)
            _write_grade(c, pick_id, res, a, "owner", note or "stat typed by hand", p["p_model"], now,
                         review=review, evidence=ev, cause=cause)
        _settle_entry(c, p["entry_id"], now)
        eid = p["entry_id"]
    return entry(eid)


def add_schedule_rows(entry_id, text, now=None):
    """DraftKings' payout for a reduced entry (or any row the owner left out)."""
    _ensure()
    now = now or time.time()
    with store._lock, store._conn() as c:
        e = c.execute("SELECT n_picks, schedule FROM pick6_entries WHERE id=?", (entry_id,)).fetchone()
        if not e:
            return None
        old = json.loads(e["schedule"])
        new = parse_schedule(text, e["n_picks"])
        rows = parse_schedule([r for r in old if (r[0], r[1]) not in {(x[0], x[1]) for x in new}] + new,
                              e["n_picks"])
        c.execute("UPDATE pick6_entries SET schedule=? WHERE id=?", (json.dumps(rows), entry_id))
        _settle_entry(c, entry_id, now)
    return entry(entry_id)


def delete_entry(entry_id):
    _ensure()
    with store._lock, store._conn() as c:
        c.execute("DELETE FROM pick6_picks WHERE entry_id=?", (entry_id,))
        n = c.execute("DELETE FROM pick6_entries WHERE id=?", (entry_id,)).rowcount
    return n > 0


# ---- reads -----------------------------------------------------------------------------
def entry(entry_id):
    _ensure()
    got = listing(ids=[entry_id])
    return got[0] if got else None


def listing(limit=30, offset=0, ids=None):
    """The newest entries with their picks (a page, never the whole table)."""
    _ensure()
    limit = max(1, min(int(limit), 100))
    with store._lock, store._conn() as c:
        if ids:
            q = c.execute(f"SELECT * FROM pick6_entries WHERE id IN ({','.join('?' * len(ids))})", list(ids))
        else:
            q = c.execute("SELECT * FROM pick6_entries ORDER BY id DESC LIMIT ? OFFSET ?", (limit, max(0, int(offset))))
        entries = [dict(r) for r in q]
        if not entries:
            return []
        by = {e["id"]: e for e in entries}
        for e in entries:
            e["schedule"] = json.loads(e["schedule"])
            e["picks"] = []
        for r in c.execute(f"""SELECT id, entry_id, idx, player, stat, stat_label, line, side, p_model, model_src,
                               result, actual, graded_by, graded_at, note, review, evidence, void_cause, kind
                               FROM pick6_picks
                               WHERE entry_id IN ({','.join('?' * len(by))}) ORDER BY entry_id, idx""", list(by)):
            p = dict(r)
            ev = json.loads(p.pop("evidence")) if p.get("evidence") else None
            p["evidence"] = _ev_text(ev) if ev else None
            p["decisions"] = list(DECISIONS.get(p["review"] or "", ())) if p["result"] == "review" else []
            by[r["entry_id"]]["picks"].append(p)
    return entries


def _flag(n):
    """The small-sample label the record prints beside a figure."""
    if n < 30:
        return "small sample"
    if n < 100:
        return "modest sample"
    return None


def _rate(h, n):
    if not n:
        return None, None
    p = h / n
    return round(100 * p, 1), round(100 * 1.96 * math.sqrt(p * (1 - p) / n), 1)


def record():
    """Hit rate by sport / stat / side, Vigil's calibration where it priced the
    pick, paper ROI on settled entries -- every figure an SQL sum over the
    scores written at grade time (a read that grows with the table stays in SQL)."""
    _ensure()
    with store._lock, store._conn() as c:
        hits = []
        for r in c.execute("""SELECT sport, stat_label, side, SUM(won IS NOT NULL) n, SUM(won) h,
                              SUM(result = 'tie') t, SUM(result = 'void') v
                              FROM pick6_picks WHERE result NOT IN ('pending', 'review')
                              GROUP BY sport, stat_label, side ORDER BY n DESC, sport, stat_label, side"""):
            n, h = int(r["n"] or 0), int(r["h"] or 0)
            pct, ci = _rate(h, n)
            hits.append({"sport": r["sport"], "stat": r["stat_label"], "side": r["side"], "n": n, "hits": h,
                         "ties": int(r["t"] or 0), "voids": int(r["v"] or 0), "hit_pct": pct, "ci95": ci,
                         "flag": _flag(n)})
        calib = []
        for r in c.execute("""SELECT sport, COUNT(*) n, SUM(won) h, SUM(p_model) sp, SUM(ll) sll,
                              SUM(ll * ll) sll2, SUM(brier) sb FROM pick6_picks
                              WHERE won IS NOT NULL AND p_model IS NOT NULL
                              GROUP BY sport ORDER BY n DESC"""):
            calib.append(_calib_row(r, r["sport"]))
        tot = c.execute("""SELECT COUNT(*) n, SUM(won) h, SUM(p_model) sp, SUM(ll) sll, SUM(ll * ll) sll2,
                           SUM(brier) sb FROM pick6_picks WHERE won IS NOT NULL AND p_model IS NOT NULL""").fetchone()
        bins = []
        for r in c.execute("""SELECT MIN(CAST(p_model * 10 AS INTEGER), 9) b, COUNT(*) n, SUM(won) h, SUM(p_model) sp
                              FROM pick6_picks WHERE won IS NOT NULL AND p_model IS NOT NULL
                              GROUP BY b ORDER BY b"""):
            n = int(r["n"])
            bins.append({"bin": f"{10 * r['b']}-{10 * r['b'] + 10}%", "n": n,
                         "vigil_pct": round(100 * r["sp"] / n, 1), "hit_pct": round(100 * (r["h"] or 0) / n, 1),
                         "flag": _flag(n)})
        roi = []
        for r in c.execute("""SELECT sport, COUNT(*) n, SUM(stake) st, SUM(payout) pay, SUM(pnl) pnl,
                              SUM(payout > 0) cashed, SUM(credits) cr FROM pick6_entries
                              WHERE status = 'settled' GROUP BY sport ORDER BY n DESC"""):
            roi.append(_roi_row(r, r["sport"]))
        rt = c.execute("""SELECT COUNT(*) n, SUM(stake) st, SUM(payout) pay, SUM(pnl) pnl, SUM(payout > 0) cashed,
                          SUM(credits) cr FROM pick6_entries WHERE status = 'settled'""").fetchone()
        st = {r["status"]: {"n": int(r["n"]), "stake": round(r["st"] or 0, 2)} for r in c.execute(
            "SELECT status, COUNT(*) n, SUM(stake) st FROM pick6_entries GROUP BY status")}
        rv = {r["review"] or "other": int(r["n"]) for r in c.execute(
            "SELECT review, COUNT(*) n FROM pick6_picks WHERE result = 'review' GROUP BY review")}
        research = []
        for sport_col, group in (("sport", "GROUP BY sport, side ORDER BY sport, side"),
                                 ("'all'", "GROUP BY side ORDER BY side")):
            for r in c.execute(f"""SELECT {sport_col} sport, side, COUNT(*) n, SUM(won = 1) h,
                                   SUM(won IS NOT NULL) nv, SUM(void_cause = 'dnp') v_dnp,
                                   SUM(result = 'tie') v_tie, SUM(void_cause = 'pardon') v_pardon
                                   FROM pick6_picks WHERE result NOT IN ('pending', 'review') {group}"""):
                research.append(_research_row(r))
    return {"hit_rate": hits, "calibration": calib, "calibration_all": _calib_row(tot, "all"),
            "calibration_bins": bins, "roi": roi, "roi_all": _roi_row(rt, "all"), "status": st,
            "in_review": rv, "research": research, "tie_rule": TIE_RULE}


def wilson(k, n, z=1.96):
    """(percent, low, high): k of n with its 95% Wilson interval, which stays
    inside 0-100 and is honest at 0 of 5 or 5 of 5 (the normal one reads 0 +/- 0)."""
    if not n:
        return None, None, None
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return round(100 * p, 1), round(100 * max(0.0, mid - half), 1), round(100 * min(1.0, mid + half), 1)


def _research_row(r):
    """The owner's research split (2026-11-27), one sport and side: OUTRIGHT
    accuracy -- hits over every decided pick, a void counted as not a hit --
    accuracy CONDITIONAL on a non-void outcome (hits over hits + misses), and
    void frequency by cause (did not play, tie, pardon, other: voided by hand
    or graded before causes were kept), each with its n and 95% interval."""
    n, h, nv = int(r["n"] or 0), int(r["h"] or 0), int(r["nv"] or 0)
    dnp, tie, pardon = int(r["v_dnp"] or 0), int(r["v_tie"] or 0), int(r["v_pardon"] or 0)
    voids = n - nv
    other = max(0, voids - dnp - tie - pardon)
    pct = lambda k, d: dict(zip(("pct", "lo", "hi"), wilson(k, d)), k=k, n=d)  # noqa: E731
    return {"sport": r["sport"], "side": r["side"], "n": n, "flag": _flag(n),
            "outright": pct(h, n), "conditional": pct(h, nv), "void": pct(voids, n),
            "void_by_cause": {"dnp": pct(dnp, n), "tie": pct(tie, n), "pardon": pct(pardon, n),
                              "other": pct(other, n)}}


def _calib_row(r, sport):
    n = int(r["n"] or 0)
    if not n:
        return {"sport": sport, "n": 0}
    mll = r["sll"] / n
    var = max(0.0, (r["sll2"] or 0) / n - mll * mll)
    return {"sport": sport, "n": n, "hit_pct": round(100 * (r["h"] or 0) / n, 1),
            "vigil_pct": round(100 * r["sp"] / n, 1), "log_loss": round(mll, 4),
            "log_loss_se": round(math.sqrt(var / n), 4) if n > 1 else None,
            "brier": round(r["sb"] / n, 4), "coin_log_loss": round(math.log(2), 4), "coin_brier": 0.25,
            "flag": _flag(n)}


def _roi_row(r, sport):
    n = int(r["n"] or 0)
    if not n:
        return {"sport": sport, "n": 0}
    st = r["st"] or 0
    return {"sport": sport, "n": n, "staked": round(st, 2), "returned": round(r["pay"] or 0, 2),
            "pnl": round(r["pnl"] or 0, 2), "roi_pct": round(100 * (r["pnl"] or 0) / st, 1) if st else None,
            "cashed": int(r["cashed"] or 0), "credit_paid": int(r["cr"] or 0), "flag": _flag(n)}


def meta():
    """What the card's form offers: the sports, their stats, the rules."""
    return {"sports": [{"key": k, "label": v["label"], "auto": bool(v["grader"]),
                        "stats": [{"key": sk, "label": sv[0]} for sk, sv in v["stats"].items()]}
                       for k, v in SPORTS.items()],
            "rules": RULES, "tie_rule": TIE_RULE, "min_picks": MIN_PICKS, "max_picks": MAX_PICKS,
            # DraftKings' Pardon rules by sport, and the market kinds a hand-typed stat may be
            "pardon": {k: v["exit"] + (f" ({v['excluded']} excluded)" if v.get("excluded") else "")
                       for k, v in PARDON.items()},
            "kinds": [{"key": k, "label": v} for k, v in KINDS.items()]}
