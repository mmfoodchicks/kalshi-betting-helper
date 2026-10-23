"""Live Kalshi NBA player-prop markets (kalshi_nfl's twin for basketball).

Kalshi lists a ladder of "stat >= k" markets for every player in every game:

    KXNBAPTS-26OCT23NOPUTA-UTAJJACKSON20-25   "Jaren Jackson Jr.: 25+ points"
    KXNBAREB-26OCT23NOPUTA-UTAJJACKSON20-10   "Jaren Jackson Jr.: 10+ rebounds"
    KXNBA2D-26OCT23PORSAC-SACPACHIUWA9        "Precious Achiuwa: Double Double"

SERIES (checked against the live API 2026-10-23 00:54-00:57 UTC; counts are the
open markets in that pull, `OBSERVED_OPEN` -- 11 games were listed then and 14
by 01:09 UTC, when KXNBAPTS read 390 across 14):

    KXNBAPTS 392  points            KXNBAPRA 162  points+rebounds+assists
    KXNBAREB 427  rebounds          KXNBASTL  68  steals
    KXNBAAST 332  assists           KXNBABLK  53  blocks
    KXNBA3PT 467  made threes       KXNBASTOCK 27 steals+blocks
    KXNBA2D   66  double-double (yes/no)   KXNBA3D 20  triple-double (yes/no)
    KXNBAPTSPROT 385 / REBPROT 424 / ASTPROT 330 / 3PTPROT 467   the PROTECTED twins
    KXNBAPR, KXNBAPA, KXNBARA, KXNBAFTM, KXNBASTOCKS   exist as series, ZERO open
    markets that evening: listed here (their shape is the same by construction
    of the ticker, but nothing was parsed from a live one) and read empty until
    Kalshi opens them. KXNBALADDER*/KXNBAESCALATOR*/KXNBAONE3PT are not game
    over-lines (season-long or ladder products) and are NOT read.

SETTLEMENT -- read off the markets' own `rules_primary` / `rules_secondary`
(identical across the standard series, only the stat word changes), NOT assumed
from the NFL books:

  Standard (KXNBAPTS, ...REB, ...AST, ...3PT, ...PRA, ...STL, ...BLK, ...2D/3D)
    "If <player> records <k>+ <stat> in the <away> vs <home> Pro Basketball game
    originally scheduled for <date>, then the market resolves to Yes."
    * A player who is INACTIVE, or active but NEVER TAKES THE COURT, settles "to
      a fair price" -- the market's result is "scalar" with a settlement value
      strictly between 0 and 1, not a Yes or No. Observed (a settled-market
      pull of 697 KXNBAPTS markets over 180 player-games in 18 event tickers,
      2026-10-05 to 10-22): 18 markets (2.6%) settled scalar, in 4 event
      tickers on 3 dates (26OCT05 and 26OCT08, both PRESEASON games, and
      26OCT21), for five player-games (2.8% of 180; LeBron James, Paul George,
      Jayson Tatum, Malik Monk, Aaron Nesmith -- three of the five in preseason,
      so the regular-season rate was 2 of 174, 1.1%). Six of the eighteen carry
      expiration_value "Confirmed DNP", twelve are blank. Values, e.g. Monk
      0.51 / 0.22 / 0.08 for the 10+ / 15+ / 20+ rungs. The fair price is the
      exchange's own determination and is NOT published; it is NOT assumed to
      equal the last traded price (Nesmith 10+ settled 0.71 against a last
      trade of 0.00, 15+ settled 0.39 against 0.84), so a DNP is not assumed
      EV-neutral at any price -- model it with prop_value.refund_adjusted_sides
      and say fair_p is a guess.
    * ONCE A PLAYER ENTERS THE GAME the market settles on his recorded stat. A
      player hurt after two minutes settles NO on every over he cannot reach:
      the in-game injury risk sits with the YES buyer.
    * Postponed but begun within 48 h of the original start: stays open and
      resolves on the final result. Cancelled or not started within 48 h: every
      market settles to a fair price.
    * The market closes and expires EARLY the moment the event happens (a YES
      is paid when the threshold is reached, not at the final buzzer);
      `close_time` is start + 48 h as a backstop.
    * Double / triple double: 10+ in two (three) of points, rebounds, assists,
      steals, blocks. 2D/3D markets carry no threshold: stat "dd" / "td3".
    * Contract terms: the standard series' document is BASKETBALLENTITYSTAT.pdf;
      the four *PROT series name a different one, NBAENTITYSTATCOVERED.pdf
      (contract_terms_url on /series/KXNBAPTSPROT ... 3PTPROT, 2026-10-23).

  Protected (the *PROT series) -- same question, different DNP rule:
    "If the player enters the game during the first half but does not enter the
    game after the conclusion of the first half, including any overtime period,
    this Contract resolves to the fair price prior to the opening tip of the
    game UNLESS the player records the required number, in which case it settles
    on actual statistics. If the player enters the game after the conclusion of
    the first half, it settles on actual statistics. Injury, an injury
    designation, or being ruled out is not required for protection."
    So a protected over cannot be lost to a first-half injury, an early
    blowout benching or a rest decision that keeps the player out of the second
    half: those cases refund at the pre-tip fair price. It is a different
    contract from the standard one with the same title and ticker stem, and it
    trades at a different price (the protection is paid for in the YES ask).
    The probability that prices it is P(reach k) + P(exits early and short) x
    (the fair price), not P(reach k); `protected` is on every record so the two
    are never mixed in one ladder.

TICKER GRAMMAR (verified on all 3,620 live game markets)

    <SERIES>-<YY><MON><DD><AWAY3><HOME3>-<TEAM3><NAME...><JERSEY?>-<K>

  The date is the US EASTERN game date (26OCT22DENOKC is a 9:30 pm ET tip,
  01:30 UTC on the 23rd). The two team codes are AWAY THEN HOME -- the opposite
  of the soccer tickers -- verified against ESPN's scoreboard for all 14 games
  live on 2026-10-22/23 (every `<away>@<home>` matched, none reversed;
  `attach_games` re-checks every time and reports `order_ok`). The player
  segment is the player's own team code, an opaque name token (first initials
  plus surname, sometimes a position suffix such as SF / PG when no jersey is
  known) and usually his JERSEY NUMBER; the name comes from the title. K is
  `floor_strike + 0.5` (0 mismatches over the 3,534 live threshold markets).
  Kalshi's codes differ from ESPN's for six clubs (`KALSHI_TO_ESPN`).

QUOTES. The list endpoint carries NO integer-cent fields any more: prices come
as dollar strings (`yes_ask_dollars` "0.2400") and sizes / volume / open
interest as fixed-point strings (`volume_fp`, `open_interest_fp`,
`yes_ask_size_fp`). `_c` reads the dollar field first and falls back to the
legacy cents field, so either payload parses. A 0 bid or a 100 ask is the book's
"nothing there" sentinel and becomes None. Kalshi does not publish
`no_*_size_fp`: the NO ask's size is the YES bid's and vice versa (a NO ask IS
a resting YES bid). The quote's `ts` is the time the page was FETCHED, which is
when the price was true; Kalshi's own `updated_time` is `updated_ts`.

PRE-GAME ONLY. The list endpoint keeps serving a market while its game is being
played (CLE@PHI was in progress at the first fetch: ESPN state "in", Maxey 20+
bid 99c with no offer on 77k contracts traded). CLAUDE.md: in-game information must
never touch a number that claims to be pre-game. `attach_games` stamps each
event with ESPN's state; `pregame()` is True only for ESPN "pre" with the tip
ahead; with no ESPN match it is None (unknown), never True. Kalshi's
`occurrence_datetime` is the tip + 3 h (3 of 3 checked games), exposed as
`start_est_ts` and labelled an estimate.

Nothing here runs at import. `index()` reads the network and is for the
background builders (like kalshi_nfl.index), not for a request handler.
"""
import difflib
import re
import time

import errlog
import kalshi
import kalshi_nfl

_TTL = 120
_IDX_STALE_MAX = 45 * 60          # same figure as kalshi_nfl / the MLB index
_MAX_PAGES = 15                   # 200 a page: 3,000 markets, ~3x tonight's widest series
_RETRY_WAITS = (1.5, 4.0)
_INCOMPLETE_TTL = 30              # an incomplete build is retried sooner and never shared
_cache = {"ts": 0.0, "data": None}

# Series -> what it measures. `parts` are the box-score components summed;
# `kind` "over" is a "k+" ladder, "event" a yes/no event with no threshold.
SERIES = {
    "KXNBAPTS":  {"stat": "pts", "label": "points", "parts": ("pts",), "kind": "over", "protected": False},
    "KXNBAREB":  {"stat": "reb", "label": "rebounds", "parts": ("reb",), "kind": "over", "protected": False},
    "KXNBAAST":  {"stat": "ast", "label": "assists", "parts": ("ast",), "kind": "over", "protected": False},
    "KXNBA3PT":  {"stat": "fg3m", "label": "made threes", "parts": ("fg3m",), "kind": "over", "protected": False},
    "KXNBAPRA":  {"stat": "pra", "label": "points + rebounds + assists", "parts": ("pts", "reb", "ast"),
                  "kind": "over", "protected": False},
    "KXNBAPR":   {"stat": "pr", "label": "points + rebounds", "parts": ("pts", "reb"), "kind": "over", "protected": False},
    "KXNBAPA":   {"stat": "pa", "label": "points + assists", "parts": ("pts", "ast"), "kind": "over", "protected": False},
    "KXNBARA":   {"stat": "ra", "label": "rebounds + assists", "parts": ("reb", "ast"), "kind": "over", "protected": False},
    "KXNBASTL":  {"stat": "stl", "label": "steals", "parts": ("stl",), "kind": "over", "protected": False},
    "KXNBABLK":  {"stat": "blk", "label": "blocks", "parts": ("blk",), "kind": "over", "protected": False},
    "KXNBASTOCK": {"stat": "stl_blk", "label": "steals + blocks", "parts": ("stl", "blk"), "kind": "over",
                   "protected": False},
    "KXNBAFTM":  {"stat": "ftm", "label": "free throws made", "parts": ("ftm",), "kind": "over", "protected": False},
    "KXNBA2D":   {"stat": "dd", "label": "double-double", "parts": ("pts", "reb", "ast", "stl", "blk"),
                  "kind": "event", "protected": False},
    "KXNBA3D":   {"stat": "td3", "label": "triple-double", "parts": ("pts", "reb", "ast", "stl", "blk"),
                  "kind": "event", "protected": False},
    "KXNBAPTSPROT": {"stat": "pts", "label": "points", "parts": ("pts",), "kind": "over", "protected": True,
                     "base": "KXNBAPTS"},
    "KXNBAREBPROT": {"stat": "reb", "label": "rebounds", "parts": ("reb",), "kind": "over", "protected": True,
                     "base": "KXNBAREB"},
    "KXNBAASTPROT": {"stat": "ast", "label": "assists", "parts": ("ast",), "kind": "over", "protected": True,
                     "base": "KXNBAAST"},
    "KXNBA3PTPROT": {"stat": "fg3m", "label": "made threes", "parts": ("fg3m",), "kind": "over", "protected": True,
                     "base": "KXNBA3PT"},
}
# Open markets per series on 2026-10-23 ~00:55 UTC (fetched by research, saved
# under the shared scratch data dir; provenance for the docstring's table).
OBSERVED_OPEN = {"KXNBAPTS": 392, "KXNBAREB": 427, "KXNBAAST": 332, "KXNBA3PT": 467, "KXNBAPRA": 162,
                 "KXNBAPR": 0, "KXNBAPA": 0, "KXNBARA": 0, "KXNBASTL": 68, "KXNBABLK": 53, "KXNBASTOCK": 27,
                 "KXNBAFTM": 0, "KXNBA2D": 66, "KXNBA3D": 20, "KXNBAPTSPROT": 385, "KXNBAREBPROT": 424,
                 "KXNBAASTPROT": 330, "KXNBA3PTPROT": 467}
# Fee type and multiplier read off /series/<ticker> (2026-10-23 00:54 UTC for
# PTS / REB / AST / 3PT / PRA; the rest from a full /series listing pulled at
# 00:47 UTC). Used ONLY when the live lookup fails; a live answer that differs
# is ledgered KNBA-fee-changed and wins.
FEE_TABLE = {s: ("quadratic", 1.0) for s in OBSERVED_OPEN}

# Kalshi's club codes that are not ESPN's. Each maps only because ESPN's code is
# not itself a Kalshi code of another club this season.
KALSHI_TO_ESPN = {"NYK": "NY", "GSW": "GS", "NOP": "NO", "SAS": "SA", "UTA": "UTAH", "WAS": "WSH"}
ESPN_TO_KALSHI = {v: k for k, v in KALSHI_TO_ESPN.items()}

STAT_LABEL = {v["stat"]: v["label"] for v in SERIES.values()}

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={d}"
ESPN_ROSTER = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{t}/roster"

_MONTHS = {m: i + 1 for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"))}
_EVENT_RE = re.compile(r"^(KXNBA[A-Z0-9]+?)-(\d{2})([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})$")
_MARKET_RE = re.compile(r"^(KXNBA[A-Z0-9]+?)-(\d{2})([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})"
                        r"-([A-Z]{3})([A-Z]+?)(\d{1,2})?(?:-(\d+))?$")
_OVER_RE = re.compile(r"^\s*(\d+)\+\s*(.*?)\s*$")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def _date(yy, mon, dd):
    m = _MONTHS.get(mon)
    return f"20{yy}-{m:02d}-{dd}" if m else None


def parse_event_ticker(ticker):
    """{"series", "date" (ET, ISO), "away", "home" (Kalshi codes), "key"} from an
    event ticker like KXNBAPTS-26OCT23NOPUTA, or None. `key` ("26OCT23NOPUTA")
    is the suffix every series of one game shares."""
    m = _EVENT_RE.match(ticker or "")
    if not m:
        return None
    date = _date(m.group(2), m.group(3), m.group(4))
    if not date:
        return None
    return {"series": m.group(1), "date": date, "away": m.group(5), "home": m.group(6),
            "key": f"{m.group(2)}{m.group(3)}{m.group(4)}{m.group(5)}{m.group(6)}"}


def parse_market_ticker(ticker):
    """Full ticker -> {"series", "date", "away", "home", "key", "team" (the
    player's club, Kalshi code), "player_code" (opaque: name token + jersey),
    "name_token", "jersey" (int or None), "k" (threshold from the ticker suffix,
    None for yes/no events)}, or None for anything that is not a game prop
    (season-long props such as KXNBAONE3PT-27-... do not match)."""
    m = _MARKET_RE.match(ticker or "")
    if not m:
        return None
    date = _date(m.group(2), m.group(3), m.group(4))
    if not date or m.group(7) not in (m.group(5), m.group(6)):
        return None
    return {"series": m.group(1), "date": date, "away": m.group(5), "home": m.group(6),
            "key": f"{m.group(2)}{m.group(3)}{m.group(4)}{m.group(5)}{m.group(6)}",
            "team": m.group(7), "player_code": m.group(8) + (m.group(9) or ""),
            "name_token": m.group(8), "jersey": int(m.group(9)) if m.group(9) else None,
            "k": int(m.group(10)) if m.group(10) else None}


def parse_title(title):
    """"Jaren Jackson Jr.: 25+ points" -> {"name": "Jaren Jackson Jr.", "k": 25,
    "stat_text": "points", "event": None}; "Precious Achiuwa: Double Double" ->
    {"name": ..., "k": None, "stat_text": "", "event": "Double Double"}; None when
    there is no "Name: ..." shape."""
    if ":" not in (title or ""):
        return None
    name, rest = title.split(":", 1)
    name = name.strip()
    if not name:
        return None
    m = _OVER_RE.match(rest)
    if m:
        return {"name": name, "k": int(m.group(1)), "stat_text": m.group(2), "event": None}
    return {"name": name, "k": None, "stat_text": "", "event": rest.strip()}


def norm_name(name):
    """Match key shared with kalshi_nfl / nfl_adp (accents, Jr/Sr/II..V and
    punctuation dropped): "Jaren Jackson Jr." -> "jaren jackson"."""
    return kalshi_nfl._norm(name)


def squash_name(name):
    """Letters only, no spaces: "P.J. Washington" and "PJ Washington" both ->
    "pjwashington" (norm_name keeps "p j washington" apart from "pj washington")."""
    return norm_name(name).replace(" ", "")


def _c(m, field):
    """A price in cents from a market dict: the `<field>_dollars` string first,
    else the legacy integer-cents `<field>`; None when absent."""
    v = m.get(field + "_dollars")
    try:
        if v is not None:
            return round(float(v) * 100.0, 3)
        v = m.get(field)
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _fp(m, field, default=0.0):
    try:
        return float(m.get(field))
    except (TypeError, ValueError):
        return default


def _clean(c):
    """A quote strictly inside (0, 100) cents is a price; 0 and 100 are the
    empty-book sentinels (kalshi_nfl._quote's rule)."""
    return c if (c is not None and 0.0 < c < 100.0) else None


def market_block(m, fetched_ts=None, fee_type=None, fee_multiplier=None):
    """The payload contract's `market` block for one raw Kalshi market dict:
    {"ticker", "yes_bid", "yes_ask", "no_bid", "no_ask" (cents or None), "ts",
    "volume", "oi", "fee_type"} plus fee_multiplier, last, volume_24h, the four
    sizes (NO sizes are the YES book's, mirrored), updated_ts, book
    ("two_sided" | "ask_only" | "bid_only" | "empty")."""
    yb, ya = _clean(_c(m, "yes_bid")), _clean(_c(m, "yes_ask"))
    nb, na = _clean(_c(m, "no_bid")), _clean(_c(m, "no_ask"))
    last = _c(m, "last_price")
    ysz_a, ysz_b = _fp(m, "yes_ask_size_fp", None), _fp(m, "yes_bid_size_fp", None)
    book = ("two_sided" if (yb is not None and ya is not None)
            else "ask_only" if ya is not None else "bid_only" if yb is not None else "empty")
    return {
        "ticker": m.get("ticker"), "yes_bid": yb, "yes_ask": ya, "no_bid": nb, "no_ask": na,
        "ts": fetched_ts, "volume": _fp(m, "volume_fp"), "oi": _fp(m, "open_interest_fp"),
        "fee_type": fee_type, "fee_multiplier": fee_multiplier,
        "last": last if (last is not None and last > 0) else None,
        "volume_24h": _fp(m, "volume_24h_fp"),
        "yes_ask_size": ysz_a, "yes_bid_size": ysz_b,
        "no_ask_size": ysz_b, "no_bid_size": ysz_a,        # a NO ask IS a resting YES bid
        "updated_ts": kalshi._parse_time(m.get("updated_time")) if m.get("updated_time") else None,
        "book": book}


def parse_market(m, fetched_ts=None, fee_type=None, fee_multiplier=None):
    """One raw market dict -> a record, or None when it is not a threshold /
    event game prop this module reads. Record keys: ticker, event, key, series,
    stat, label, parts, protected, kind, k (int, None for yes/no events), line
    (floor_strike), date, away, home, team, opp, player, player_norm,
    player_code, jersey, title, close_ts, expected_expiration_ts,
    occurrence_ts, start_est_ts (tip estimate = occurrence - 3 h), status,
    market (the contract's block), q ({"yes": ..., "no": ...} in
    kalshi_nfl._q's shape, so kalshi_nfl.believable and
    combo_engine.market_reference read it unchanged). q is built from the
    CLEANED block, so a 0c bid is "no bid" (None), not a bid of zero: that is the
    one place it differs from kalshi_nfl._q, which reads an ask-only book at 10c
    as a 10c-wide two-sided market and calls it believable."""
    tk = parse_market_ticker(m.get("ticker"))
    if not tk:
        return None
    meta = SERIES.get(tk["series"])
    t = parse_title(m.get("title"))
    if not meta or not t:
        return None
    k = None
    if meta["kind"] == "over":
        if m.get("strike_type") not in (None, "greater") or m.get("floor_strike") is None:
            return None
        k = int(round(float(m["floor_strike"]) + 0.5))
        if t["k"] is not None and t["k"] != k:
            errlog.note("KNBA-parse", msg=f"{m.get('ticker')}: title k={t['k']} != floor_strike+0.5={k}")
            return None
    occ = kalshi._parse_time(m.get("occurrence_datetime")) if m.get("occurrence_datetime") else None
    opp = tk["home"] if tk["team"] == tk["away"] else tk["away"]
    block = market_block(m, fetched_ts, fee_type, fee_multiplier)
    return {
        "ticker": m["ticker"], "event": m.get("event_ticker"), "key": tk["key"], "series": tk["series"],
        "stat": meta["stat"], "label": meta["label"], "parts": meta["parts"], "protected": meta["protected"],
        "kind": meta["kind"], "k": k, "line": m.get("floor_strike"),
        "date": tk["date"], "away": tk["away"], "home": tk["home"], "team": tk["team"], "opp": opp,
        "player": t["name"], "player_norm": norm_name(t["name"]), "player_code": tk["player_code"],
        "jersey": tk["jersey"], "title": m.get("title"),
        "close_ts": kalshi._parse_time(m.get("close_time")) if m.get("close_time") else None,
        "expected_expiration_ts": (kalshi._parse_time(m.get("expected_expiration_time"))
                                   if m.get("expected_expiration_time") else None),
        "occurrence_ts": occ, "start_est_ts": (occ - 3 * 3600) if occ else None,
        "status": m.get("status"),
        "market": block,
        "q": {"yes": quote_from_block(block, "yes"), "no": quote_from_block(block, "no")},
    }


# ---------------------------------------------------------------------------
# Quotes: believable / reference, on a record or on a bare `market` block
# ---------------------------------------------------------------------------
def quote_from_block(block, side="yes"):
    """kalshi_nfl._q's quote shape ({"ask", "bid", "mid", "spread", "size",
    "vol", "oi"}) from a payload `market` block, or None when that side has no
    ask. A missing bid stays None (the block already turned the 0c sentinel into
    None), so a one-sided book reads as one-sided to combo_engine rather than as
    a 0c bid."""
    ask, bid = block.get(f"{side}_ask"), block.get(f"{side}_bid")
    if ask is None or not (0 < ask < 100):
        return None
    if bid is None or not (0 < bid <= ask):
        bid = None
    return {"ask": ask, "bid": bid, "mid": ((bid + ask) / 2.0) if bid is not None else ask,
            "spread": (ask - bid) if bid is not None else None,
            "size": block.get(f"{side}_ask_size") or 0.0,
            "vol": block.get("volume") or 0.0, "oi": block.get("oi") or 0.0}


def _yes_quote(x):
    if "q" in x and isinstance(x["q"], dict):          # a record
        return x["q"].get("yes")
    return quote_from_block(x, "yes")                  # a bare block


def believable(x, max_spread=kalshi_nfl._MAX_PROP_SPREAD):
    """Is this YES book an opinion or just a listing? kalshi_nfl.believable's
    rule exactly (two-sided, spread <= max_spread cents, some volume or open
    interest) applied to a record or a `market` block."""
    return kalshi_nfl.believable(_yes_quote(x), max_spread)


def market_ref(x):
    """(reference P(YES) 0-1, quality 0-1) -- combo_engine.market_reference on
    the YES quote of a record or block: the mid when two-sided and tight (quality
    from spread and depth), the ask at fixed low quality when one-sided, (None,
    0) when there is no ask."""
    import combo_engine
    return combo_engine.market_reference(_yes_quote(x))


# ---------------------------------------------------------------------------
# Fetching (paging that never returns part of a series)
# ---------------------------------------------------------------------------
def _fetch(series, max_pages=_MAX_PAGES):
    """Every open market of one series. kalshi_nfl._fetch's contract, with KNBA
    ledger IDs: a page that still fails after two retries FAILS the fetch when
    earlier pages were read (raises), because a partial series is not an index
    -- Kalshi pages newest listings first, so the missing tail would be the
    games nearest tip-off. A series whose very first page fails raises too (it
    used to be returned empty, indistinguishable from a series with no
    markets). A cursor still open after max_pages is ledgered, not hidden."""
    out, cursor = [], None
    for page in range(max_pages):
        url = f"{kalshi.BASE}/markets?series_ticker={series}&status=open&limit=200"
        if cursor:
            url += f"&cursor={cursor}"
        d, err = None, None
        for wait in (0.0,) + tuple(_RETRY_WAITS):
            if wait:
                time.sleep(wait)
            try:
                d = kalshi._get_json(url)
                break
            except Exception as e:
                err = e
        if d is None:
            errlog.note("KNBA-fetch", err, path=series)
            raise RuntimeError(f"{series}: page {page + 1} failed after {len(out)} markets "
                               "-- a partial series is not an index")
        out.extend(d.get("markets") or [])
        cursor = d.get("cursor")
        if not cursor:
            break
    else:
        errlog.note("KNBA-truncated", msg=f"{series}: {len(out)} markets over {max_pages} pages "
                                          "and the cursor is still open")
    return out


def _fee_for(series, fee_lookup=None):
    """(fee_type, fee_multiplier) for a series: the live record, else FEE_TABLE.
    A live answer that disagrees with the table is ledgered and wins."""
    live = (fee_lookup or kalshi.series_info)(series) or {}
    table = FEE_TABLE.get(series, (None, 1.0))
    ft = live.get("fee_type")
    if ft is None:
        return table
    try:
        mult = float(live.get("fee_multiplier")) if live.get("fee_multiplier") is not None else 1.0
    except (TypeError, ValueError):
        mult = 1.0
    if (ft, mult) != table and series in FEE_TABLE:
        errlog.note("KNBA-fee-changed", msg=f"{series}: table {table}, live {(ft, mult)}")
    return ft, mult


def _build(fetch=None, series=None, now=None, fee_lookup=None):
    """The whole index. `fetch(series) -> [raw market dict]` and `fee_lookup`
    are injectable for tests. A series whose fetch raises is recorded in
    `failed` and the index is marked `complete: False`."""
    fetch = fetch or _fetch
    now = time.time() if now is None else now
    recs, events, rules, counts, failed = [], {}, {}, {}, []
    for s in (series or list(SERIES)):
        try:
            raw = fetch(s)
        except Exception as e:
            errlog.note("KNBA-series", e, path=s)
            failed.append(s)
            continue
        ft, mult = _fee_for(s, fee_lookup)
        n = 0
        for m in raw:
            r = parse_market(m, now, ft, mult)
            if r is None:
                if m.get("ticker"):
                    counts.setdefault("_skipped", []).append(m["ticker"])
                continue
            recs.append(r)
            n += 1
            ev = events.setdefault(r["key"], {
                "key": r["key"], "date": r["date"], "away": r["away"], "home": r["home"],
                "away_espn": KALSHI_TO_ESPN.get(r["away"], r["away"]),
                "home_espn": KALSHI_TO_ESPN.get(r["home"], r["home"]),
                "close_ts": r["close_ts"], "start_est_ts": r["start_est_ts"], "series": []})
            if s not in ev["series"]:
                ev["series"].append(s)
            if s not in rules and m.get("rules_secondary") is not None:
                rules[s] = {"primary_example": m.get("rules_primary"), "secondary": m.get("rules_secondary")}
        counts[s] = n
    skipped = counts.pop("_skipped", [])
    if skipped:
        errlog.note("KNBA-skipped", msg=f"{len(skipped)} markets did not parse, e.g. {skipped[:3]}")
    recs.sort(key=lambda r: (r["key"], r["series"], r["player_norm"], r["k"] or 0))
    # by_line: one position per (game, stat, contract, player, rung). Two
    # DIFFERENT players with the same normalised name in one game would collide
    # here; such a line is left out of by_line (so market_for_line returns None
    # rather than the wrong man's book), ledgered KNBA-dup-line, and stays
    # reachable through by_line_team, which carries the club.
    by_line, by_line_team, clash = {}, {}, set()
    for i, r in enumerate(recs):
        kk = (r["key"], r["stat"], r["protected"], r["player_norm"], r["k"])
        by_line_team[kk + (r["team"],)] = i
        if kk in by_line and recs[by_line[kk]]["team"] != r["team"]:
            clash.add(kk)
        by_line.setdefault(kk, i)
    for kk in clash:
        by_line.pop(kk, None)
        errlog.note("KNBA-dup-line", msg=f"two players named {kk[3]!r} in {kk[0]}")
    return {"ts": now, "complete": not failed, "failed": failed, "counts": counts,
            "skipped": len(skipped), "events": events, "rules": rules, "markets": recs,
            "by_line": by_line, "by_line_team": by_line_team}


def index(max_age=_TTL):
    """The Kalshi NBA prop index, shared across workers with a last-good
    fallback (kalshi_nfl.index's policy): a fresh sibling copy, else a build
    that publishes whenever it is COMPLETE -- every series answered -- even if
    it holds zero markets (before listings open, or in the off-season, an empty
    index is the truth, and not publishing it would make every builder re-pay
    ~50 GETs each `max_age`). An INCOMPLETE build (some series failed) or a raised
    one is never published: it keeps the last good copy, up to 45 minutes old,
    and tries again after `max_age`; with no earlier copy at all the incomplete
    build is served flagged `complete: False` and retried after 30 s.
    Network: ~25-60 GETs (18 series, up to 15 pages each); for the background
    builders, never a request handler."""
    import boardshare
    now = time.time()
    if _cache["data"] and now - _cache["ts"] <= max_age:
        return _cache["data"]
    disk, age = boardshare.get("kalshi_nba_idx", max_age)
    if disk:
        _cache["data"], _cache["ts"] = disk, now - age
        return disk
    try:
        built = _build()
    except Exception as e:
        errlog.note("KNBA-index-build", e)
        built = None
    if built and built["complete"]:
        _cache["data"], _cache["ts"] = built, now
        boardshare.put("kalshi_nba_idx", built)
        return built
    stale = _cache["data"]
    if not stale:
        stale, _ = boardshare.get("kalshi_nba_idx", _IDX_STALE_MAX)
    if stale:
        _cache["data"], _cache["ts"] = stale, now        # keep last good; retry later
        return stale
    if built:                                            # incomplete, nothing better: serve it flagged
        _cache["data"], _cache["ts"] = built, now - (max_age - _INCOMPLETE_TTL)
        errlog.note("KNBA-index-incomplete", msg=f"failed series: {built['failed']}")
        return built
    errlog.note("KNBA-index-empty", msg="index build failed with no usable fallback")
    return {"ts": now, "complete": False, "failed": list(SERIES), "counts": {}, "skipped": 0,
            "events": {}, "rules": {}, "markets": []}


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def markets(idx=None, *, key=None, stat=None, series=None, player=None, team=None, date=None,
            protected=None, believable_only=False):
    """Records of an index (default: the live one) filtered by event key
    ("26OCT23NOPUTA"), stat ("pts"), series, normalised player name (or any
    form norm_name maps to it), team (Kalshi code), ET date, protected flag, and
    optionally only believable books."""
    idx = idx or index()
    pn = norm_name(player) if player else None
    out = []
    for r in idx.get("markets") or []:
        if key and r["key"] != key or stat and r["stat"] != stat or series and r["series"] != series:
            continue
        if pn and r["player_norm"] != pn or team and r["team"] != team or date and r["date"] != date:
            continue
        if protected is not None and r["protected"] != protected:
            continue
        if believable_only and not believable(r):
            continue
        out.append(r)
    return out


def ladders(idx=None, *, key=None, protected=False, stat=None):
    """{(game key, stat, player_norm): {"name", "team", "opp", "key", "protected",
    "rungs": [(k, p_yes), ...], "n_listed", "n_dropped"}} for the "k+" ladders.

    The GAME KEY is part of the dict key: Kalshi lists tomorrow's games while
    today's are on, so a player in a back-to-back has two ladders in one index,
    and pooling them (the first version keyed by (stat, player) alone) ran both
    games' rungs through one PAVA fit with duplicated thresholds. `key=` still
    restricts to one game. Two same-named players in one game are kept apart:
    the second is skipped and ledgered KNBA-dup-player.

    Only BELIEVABLE rungs enter (a listing nobody quotes sits at 5c / 92c and
    de-vigs to 0.49 at every rung -- kalshi_nfl.prop_ladders' lesson). p is the
    YES MID: the NO side of the same book mirrors it, so the mid / (mid + mid)
    de-vig of kalshi_nfl.prop_ladders is the identity here. Rungs are made
    non-increasing in k with prop_dist.enforce_monotone (PAVA), and a ladder
    whose raw mids needed fixing is flagged `monotone_fixed`. Standard and
    protected ladders are different contracts and are never merged."""
    import prop_dist
    out = {}
    for r in markets(idx, key=key, protected=protected, stat=stat):
        if r["kind"] != "over":
            continue
        e = out.setdefault((r["key"], r["stat"], r["player_norm"]), {
            "name": r["player"], "team": r["team"], "opp": r["opp"], "key": r["key"],
            "protected": r["protected"], "rungs": [], "n_listed": 0, "n_dropped": 0})
        if e["team"] != r["team"]:
            errlog.note("KNBA-dup-player", msg=f"{r['player_norm']!r} lists under {e['team']} and {r['team']} in {r['key']}")
            continue
        e["n_listed"] += 1
        if believable(r):
            e["rungs"].append((r["k"], r["q"]["yes"]["mid"] / 100.0))
        else:
            e["n_dropped"] += 1
    final = {}
    for k_, e in out.items():
        if not e["rungs"]:
            continue
        e["rungs"].sort()
        ks = [a for a, _ in e["rungs"]]
        raw = [b for _, b in e["rungs"]]
        fixed = prop_dist.enforce_monotone(ks, raw)
        e["monotone_fixed"] = any(abs(a - b) > 1e-12 for a, b in zip(raw, fixed))
        e["rungs"] = list(zip(ks, fixed))
        final[k_] = e
    return final


def event_key(idx, team_a, team_b, date=None):
    """The event key ("26OCT23NOPUTA") of the game between two clubs, given in
    EITHER Kalshi's codes or ESPN's, in either order; `date` (ET, ISO) breaks a
    tie when the same pair plays twice in the index. None when not listed."""
    def kal(c):
        c = (c or "").upper()
        return ESPN_TO_KALSHI.get(c, c)
    want = frozenset((kal(team_a), kal(team_b)))
    hits = [k for k, e in (idx or index()).get("events", {}).items()
            if frozenset((e["away"], e["home"])) == want and (date is None or e["date"] == date)]
    return hits[0] if len(hits) == 1 else None


def market_for_line(idx, *, key, stat, player, k, protected=False, team=None):
    """The payload contract's `market` block (a copy) for one line -- event key,
    stat ("pts"), player name (any spelling norm_name folds), threshold k, and
    which contract (standard / protected) -- or None when Kalshi lists no such
    rung. A rung that is listed but dead still returns its block: ask the
    caller's believable() whether it is an opinion. `team` (either code set)
    names the player's club and is required only to separate two same-named
    players in one game, whose lines are otherwise None (KNBA-dup-line)."""
    idx = idx or index()
    kk = (key, stat, bool(protected), norm_name(player), k)
    if team:
        t = (team or "").upper()
        pos = (idx.get("by_line_team") or {}).get(kk + (ESPN_TO_KALSHI.get(t, t),))
    else:
        pos = (idx.get("by_line") or {}).get(kk)
    if pos is None:
        return None
    return dict(idx["markets"][pos]["market"])


def players(idx=None):
    """Distinct (team, player) pairs listed: [{"team", "player", "player_norm",
    "jersey", "player_code", "key"}], one per player per game."""
    seen, out = set(), []
    for r in (idx or index()).get("markets") or []:
        pk = (r["key"], r["team"], r["player_norm"])
        if pk in seen:
            continue
        seen.add(pk)
        out.append({"key": r["key"], "team": r["team"], "player": r["player"],
                    "player_norm": r["player_norm"], "jersey": r["jersey"],
                    "player_code": r["player_code"]})
    return out


# ---------------------------------------------------------------------------
# Games: ESPN scoreboard cross-check (home / away, tip, state)
# ---------------------------------------------------------------------------
def espn_games(payload):
    """{frozenset({away, home}): {"id", "home", "away", "start_ts", "state"}}
    from one ESPN NBA scoreboard payload; codes are ESPN's."""
    out = {}
    for e in payload.get("events") or []:
        c = (e.get("competitions") or [{}])[0]
        side = {x.get("homeAway"): ((x.get("team") or {}).get("abbreviation") or "").upper()
                for x in c.get("competitors") or []}
        if not side.get("home") or not side.get("away"):
            continue
        out[frozenset((side["home"], side["away"]))] = {
            "id": e.get("id"), "home": side["home"], "away": side["away"],
            "start_ts": kalshi._parse_time(e.get("date")) if e.get("date") else None,
            "state": ((c.get("status") or {}).get("type") or {}).get("state")}
    return out


def attach_games(idx, payloads_by_date):
    """Stamp each event in `idx["events"]` with ESPN's view of the game:
    event["game"] = {"espn_id", "home", "away" (ESPN codes), "home_kalshi",
    "away_kalshi", "start_ts", "state", "order_ok"}, order_ok True when the
    ticker's AWAY-then-HOME order agrees with ESPN's. `payloads_by_date` maps an
    ET date "YYYY-MM-DD" to a scoreboard payload (the caller fetches; this
    function is pure). Events ESPN does not list get game None. Returns
    {"matched", "unmatched": [keys], "order_ok", "order_bad": [keys]}."""
    games = {d: espn_games(p) for d, p in (payloads_by_date or {}).items()}
    res = {"matched": 0, "unmatched": [], "order_ok": 0, "order_bad": []}
    for key, ev in (idx.get("events") or {}).items():
        pair = frozenset((ev["away_espn"], ev["home_espn"]))
        g = (games.get(ev["date"]) or {}).get(pair)
        if g is None:
            for d, gm in games.items():             # the ET day boundary: try the others
                if pair in gm:
                    g = gm[pair]
                    break
        if g is None:
            ev["game"] = None
            res["unmatched"].append(key)
            continue
        ok = (g["away"] == ev["away_espn"] and g["home"] == ev["home_espn"])
        ev["game"] = {"espn_id": g["id"], "home": g["home"], "away": g["away"],
                      "home_kalshi": ESPN_TO_KALSHI.get(g["home"], g["home"]),
                      "away_kalshi": ESPN_TO_KALSHI.get(g["away"], g["away"]),
                      "start_ts": g["start_ts"], "state": g["state"], "order_ok": ok}
        res["matched"] += 1
        if ok:
            res["order_ok"] += 1
        else:
            res["order_bad"].append(key)
    return res


def pregame(idx, key, now=None):
    """True only when ESPN lists the game as "pre" with its tip still ahead; False
    when it is live or finished; None when ESPN has not been consulted for this
    game (unknown -- never treated as pre-game)."""
    g = ((idx.get("events") or {}).get(key) or {}).get("game")
    if not g:
        return None
    now = time.time() if now is None else now
    return g["state"] == "pre" and (g["start_ts"] is None or g["start_ts"] > now)


def load_scoreboards(dates, get=None):
    """{date: ESPN scoreboard payload} for ET dates "YYYY-MM-DD" (network). A
    date that fails is left out and ledgered KNBA-espn-board."""
    if get is None:
        import racing
        get = racing._get_json
    out = {}
    for d in sorted(set(dates)):
        try:
            out[d] = get(ESPN_SCOREBOARD.format(d=d.replace("-", "")))
        except Exception as e:
            errlog.note("KNBA-espn-board", e, path=d)
    return out


# ---------------------------------------------------------------------------
# Players: Kalshi name -> ESPN athlete id
# ---------------------------------------------------------------------------
def parse_roster(payload):
    """ESPN team-roster payload -> [{"id", "name", "norm", "squash", "jersey"
    (int or None; ESPN writes "00" and Kalshi "0"), "pos"}]."""
    out = []
    for a in (payload.get("athletes") or []):
        nm = a.get("fullName") or a.get("displayName") or ""
        try:
            jersey = int(a.get("jersey")) if a.get("jersey") not in (None, "") else None
        except (TypeError, ValueError):
            jersey = None
        out.append({"id": str(a.get("id")), "name": nm, "norm": norm_name(nm),
                    "squash": squash_name(nm), "jersey": jersey,
                    "pos": ((a.get("position") or {}).get("abbreviation"))})
    return out


def load_rosters(get=None, teams=None):
    """{ESPN team code: [roster rows]} for all thirty clubs (network: one GET
    each). Cached 6 h across workers. A team that fails is left out and
    ledgered KNBA-roster; match_player then misses its players rather than
    guessing."""
    import boardshare
    if get is None:
        disk, _age = boardshare.get("kalshi_nba_rosters", 6 * 3600)
        if disk:
            return disk
        import racing
        get = racing._get_json
    import nba_pbp
    out = {}
    for t in (teams or sorted(nba_pbp.FRANCHISES)):
        try:
            out[t] = parse_roster(get(ESPN_ROSTER.format(t=t)))
        except Exception as e:
            errlog.note("KNBA-roster", e, path=t)
    if out and len(out) >= 28:
        boardshare.put("kalshi_nba_rosters", out)
    return out


def match_player(name, team, jersey, rosters, strict_team=False):
    """Kalshi (name, club code, jersey) -> ESPN athlete, or None.

    Returns {"athlete_id", "espn_name", "method", "team_ok"} where method is,
    in order of trust:
      team_name        exact normalised name on the club's roster
      team_squash      same letters ignoring spaces / dots (P.J. vs PJ)
      team_jersey_name jersey number equal AND name similarity >= 0.75
      team_last_init   same surname and first initial, unique on the roster
      league_name      exact normalised name, unique across the league (a
                       player traded since the roster was cached: team_ok False)
      team_jersey_fuzzy jersey equal and name similarity >= 0.6, unique
    Nothing weaker is accepted: a miss is reported, not guessed. With
    strict_team the league-wide step is skipped."""
    et = KALSHI_TO_ESPN.get(team, team)
    roster = rosters.get(et) or []
    nn, sq = norm_name(name), squash_name(name)

    def hit(row, method, team_ok=True):
        return {"athlete_id": row["id"], "espn_name": row["name"], "method": method, "team_ok": team_ok}

    for row in roster:
        if row["norm"] == nn:
            return hit(row, "team_name")
    for row in roster:
        if row["squash"] == sq:
            return hit(row, "team_squash")
    if jersey is not None:
        same = [(difflib.SequenceMatcher(None, row["norm"], nn).ratio(), row)
                for row in roster if row["jersey"] == jersey]
        same.sort(key=lambda x: -x[0])
        if same and same[0][0] >= 0.75:
            return hit(same[0][1], "team_jersey_name")
    parts = nn.split()
    if len(parts) >= 2:
        cands = [row for row in roster if row["norm"].split()[-1:] == parts[-1:]
                 and row["norm"][:1] == nn[:1]]
        if len(cands) == 1:
            return hit(cands[0], "team_last_init")
    if not strict_team:
        league = [row for t, rows in rosters.items() for row in rows if row["norm"] == nn]
        if len(league) == 1:
            return hit(league[0], "league_name", team_ok=False)
    if jersey is not None:
        same = [(difflib.SequenceMatcher(None, row["norm"], nn).ratio(), row)
                for row in roster if row["jersey"] == jersey]
        same = [x for x in same if x[0] >= 0.6]
        if len(same) == 1:
            return hit(same[0][1], "team_jersey_fuzzy")
    return None


def match_players(idx, rosters):
    """Match every distinct player in `idx` and say how it went:
    {"matches": {(team, player_norm): match or None}, "n_players", "n_matched",
    "rate", "by_method": {...}, "misses": [{"team", "player", "jersey",
    "player_code", "key"}], "markets_matched", "markets_total", "market_rate"}.
    A player is one (Kalshi club, normalised name) however many games / rungs
    list him."""
    matches, first = {}, {}
    for r in idx.get("markets") or []:
        pk = (r["team"], r["player_norm"])
        if pk not in first:
            first[pk] = r
    by_method, misses = {}, []
    for pk, r in first.items():
        mt = match_player(r["player"], r["team"], r["jersey"], rosters)
        matches[pk] = mt
        if mt:
            by_method[mt["method"]] = by_method.get(mt["method"], 0) + 1
        else:
            misses.append({"team": r["team"], "player": r["player"], "jersey": r["jersey"],
                           "player_code": r["player_code"], "key": r["key"]})
    mk_total = len(idx.get("markets") or [])
    mk_ok = sum(1 for r in (idx.get("markets") or []) if matches.get((r["team"], r["player_norm"])))
    n = len(first)
    return {"matches": matches, "n_players": n, "n_matched": n - len(misses),
            "rate": ((n - len(misses)) / n) if n else None, "by_method": by_method,
            "misses": sorted(misses, key=lambda x: (x["team"], x["player"])),
            "markets_matched": mk_ok, "markets_total": mk_total,
            "market_rate": (mk_ok / mk_total) if mk_total else None}


# ---------------------------------------------------------------------------
# Order book (one market, on demand)
# ---------------------------------------------------------------------------
def fetch_orderbook(ticker, get=None):
    """Depth for ONE market: GET /markets/{ticker}/orderbook, returned as
    {"ticker", "ts", "yes_asks", "no_asks" ([(price_c, size)], cheapest first --
    what buying that side costs, level by level), "yes_bids", "no_bids"
    ([(price_c, size)], best first)}. Feed yes_asks / no_asks to
    prop_value.walk_book. The book lists bids only; asks are the other side's
    bids at 100 - price (prop_value.asks_from_orderbook). Raises on a failed
    GET after the shared client's own retries (ledgered KAL-fetch there)."""
    import prop_value
    get = get or kalshi._get_json
    ob = get(f"{kalshi.BASE}/markets/{ticker}/orderbook")

    def bids(side):
        book = ob.get("orderbook_fp") or ob.get("orderbook") or {}
        rows, scale = book.get(f"{side}_dollars"), 100.0
        if rows is None:
            rows, scale = book.get(side), 1.0
        out = []
        for r in rows or []:
            try:
                out.append((round(float(r[0]) * scale, 4), float(r[1])))
            except (TypeError, ValueError, IndexError):
                continue
        out.sort(key=lambda x: -x[0])
        return out

    return {"ticker": ticker, "ts": time.time(),
            "yes_asks": prop_value.asks_from_orderbook(ob, "yes"),
            "no_asks": prop_value.asks_from_orderbook(ob, "no"),
            "yes_bids": bids("yes"), "no_bids": bids("no")}
