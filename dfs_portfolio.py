"""Portfolio controls and roster restrictions for OUR entries (2026-11-26).

The owner's priorities 5 and 6: cap how much of a multi-entry portfolio one
player or one team takes, cap how many players any two entries share, show
how concentrated every portfolio is even when nothing is capped, and let a
build carry its own roster restrictions -- the stack, the bring-back, a
per-team maximum and a minimum salary -- beside the player picker's
exclusions. Everything here rides the picker's `pick` (nfl_dfs.pick_parse):
it binds OUR lineups only. The FIELD is never constrained by the owner's
choices, exactly as the picker's own rules (CLAUDE.md, "The DFS player
picker").

Three pieces:
  * parsing: `parse_rules`, `parse_port` (shape and range; names resolve in
    nfl_dfs.pick_resolve against the slate);
  * the cover's caps: `Limits`, read by dfs_tourney.greedy_cover at every
    step -- a candidate that would break a cap is skipped, the best of the
    rest is taken, and a cover that runs out of feasible candidates stops
    short and says which cap did it;
  * reading a portfolio: `concentration` (per-player and per-team exposure,
    the largest pairwise overlap, flags), pure Python so the sheet's web
    worker and CI's numpy-less runner compute it too. static/dfspick.js
    carries the same definition (dfsConcentration) for whatever prefix the
    tab shows; a guard runs both on the same rows.

Percentages are whole numbers (0-100), as the tab shows them, and every cap
is a COUNT: at most floor(pct x entries / 100) entries hold a capped player,
and at least one for any cap above 0 (cap_count). Integer arithmetic, so 50%
of 20 is exactly 10 and never 9.999.
"""

import collections

try:
    import numpy as np
except ImportError:                      # the server: no numpy; the cover and the masks are the PC's
    np = None

# A player in more than this share of the entries is flagged when the owner set
# no cap of his own (a cap the cover enforced cannot be exceeded, so with one
# set the flag threshold is that cap). Half the entries is where a multi-entry
# stops being a spread and starts being one bet several times over; it is a
# reading aid, not a fitted number.
WARN_PCT = 50

# Classic rosters: QB + up to five pass-catchers (three WR, the TE, a WR at
# FLEX); "QB plus N" therefore runs 1-4 (five would leave no back at all).
STACK_MAX = 4
SALARY_CAP = 50000
CLASSIC_SIZE = 9
SHOWDOWN_SIZE = 6

Rules = collections.namedtuple("Rules", "stack bring team_max min_salary")
# players: {raw DraftKings name: pct} once resolved (parsed: [(name, team, id, pct)])
Port = collections.namedtuple("Port", "player_pct players team_pct max_overlap")


def _int_in(v, lo, hi, what):
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        raise ValueError(f"{what} must be a whole number")
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a whole number")
    if f != int(f):
        raise ValueError(f"{what} must be a whole number")
    n = int(f)
    if not lo <= n <= hi:
        raise ValueError(f"{what} must be between {lo:,} and {hi:,}")
    return n


def parse_rules(raw):
    """The pick's `rules` -- {stack, bring, team_max, min_salary}, each optional
    -- normalised to a plain dict holding only what is set, or None. Ranges
    only; whether a slate can meet them is pick_resolve's arithmetic and the
    build's search. ValueError on a malformed one.

      stack       the quarterback plus at least N of his receivers and tight
                  ends (classic only), 1-4
      bring       "required" (a receiver or tight end from the quarterback's
                  opponent) or "forbidden" (none), classic only -- the house
                  definition of a bring-back (nfl_dfs._build_one)
      team_max    at most N players from one team, 1-8 (a showdown's six over
                  two teams puts three on one, so 3-5 there; resolve checks)
      min_salary  at least $N of the $50,000 cap used"""
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise ValueError("rules must be an object: {stack, bring, team_max, min_salary}")
    out = {}
    s = _int_in(raw.get("stack"), 1, STACK_MAX, "rules.stack (receivers stacked with the quarterback)")
    if s is not None:
        out["stack"] = s
    b = raw.get("bring")
    if b not in (None, "", "any"):
        if b not in ("required", "forbidden"):
            raise ValueError("rules.bring must be required, forbidden or any")
        out["bring"] = b
    t = _int_in(raw.get("team_max"), 1, 8, "rules.team_max (players from one team)")
    if t is not None:
        out["team_max"] = t
    m = _int_in(raw.get("min_salary"), 0, SALARY_CAP, "rules.min_salary")
    if m:
        out["min_salary"] = m
    return out or None


def _port_player(x):
    """((name, team, id), pct) of one per-player cap; a parsed 4-list too (idempotent)."""
    if isinstance(x, (list, tuple)) and len(x) == 4:
        x = {"name": x[0], "team": x[1], "id": x[2], "max_pct": x[3]}
    if not isinstance(x, dict) or not str(x.get("name") or "").strip():
        raise ValueError("every per-player cap needs a name")
    pct = _int_in(x.get("max_pct"), 0, 100, f"the cap for {str(x.get('name'))[:40]}")
    if pct is None:
        raise ValueError(f"the cap for {str(x.get('name'))[:40]} needs a percentage")
    return (str(x["name"]).strip()[:80], str(x.get("team") or "").strip().upper()[:5],
            str(x.get("id") or "").strip()[:20], pct)


def parse_port(raw):
    """The pick's `portfolio` -- {player_pct, players: [{name, team, id,
    max_pct}], team_pct, max_overlap}, each optional -- as a plain dict of
    what is set, or None. ValueError on a malformed one.

      player_pct   no player in more than this % of the entries
      players      per-player overrides of that cap (0 keeps him out of every
                   entry, 100 lifts the cap for him)
      team_pct     no team LEADS more than this % of the entries: the
                   quarterback's team on a classic lineup (its stack), the
                   captain's on a showdown
      max_overlap  no two entries share more than N players (by player,
                   whatever the slot)"""
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise ValueError("portfolio must be an object: {player_pct, players, team_pct, max_overlap}")
    out = {}
    p = _int_in(raw.get("player_pct"), 1, 100, "portfolio.player_pct")
    if p is not None and p < 100:
        out["player_pct"] = p
    pl = raw.get("players") or []
    if not isinstance(pl, list):
        raise ValueError("portfolio.players must be a list")
    if len(pl) > 30:
        raise ValueError("portfolio.players names at most 30 players")
    if pl:
        out["players"] = [_port_player(x) for x in pl]
    t = _int_in(raw.get("team_pct"), 1, 100, "portfolio.team_pct")
    if t is not None and t < 100:
        out["team_pct"] = t
    o = _int_in(raw.get("max_overlap"), 0, CLASSIC_SIZE - 1, "portfolio.max_overlap")
    if o is not None:
        out["max_overlap"] = o
    return out or None


def rules_text(rules):
    """The owner's roster rules, each in words (a refusal names one; the stamp lists them)."""
    if not rules:
        return []
    out = []
    if rules.stack:
        out.append(f"the quarterback stacked with {rules.stack}+ of his receivers and tight ends")
    if rules.bring == "required":
        out.append("a bring-back (a receiver or tight end from the quarterback's opponent)")
    elif rules.bring == "forbidden":
        out.append("no bring-back (no receiver or tight end from the quarterback's opponent)")
    if rules.team_max:
        out.append(f"at most {rules.team_max} players from one team")
    if rules.min_salary:
        out.append(f"at least ${rules.min_salary:,} of salary used")
    return out


def cap_count(pct, n):
    """Entries out of `n` a `pct`% cap allows: floor(pct x n / 100), in
    integers, and never 0 for a cap above 0 -- a 10% cap on a five-entry
    portfolio would otherwise keep every capped player out of all five (and a
    global 10% cap would leave no lineup at all). 0% keeps a man out."""
    pct, n = int(pct), int(n)
    return 0 if pct <= 0 else max(1, (pct * n) // 100)


def stamp(rules=None, port=None):
    """What a build says it was built under (the pick stamp's additions)."""
    out = {}
    if rules:
        out["rules"] = {k: v for k, v in rules._asdict().items() if v is not None}
        out["rules_text"] = rules_text(rules)
    if port:
        out["portfolio"] = {"player_pct": port.player_pct, "players": dict(sorted((port.players or {}).items())),
                            "team_pct": port.team_pct, "max_overlap": port.max_overlap}
    return out


# ---- the cover's caps ---------------------------------------------------------
class Limits:
    """The owner's portfolio caps over one candidate shortlist, read by
    dfs_tourney.greedy_cover at every step. `members` (K, S) are the player
    indices of each candidate (rows aligned with the cover's matrix), `lead`
    (K,) the team code each candidate is built around (the quarterback's or
    the captain's), `pcap` the per-player cap in percent (-1: none -- the
    forced men, who are in every entry by the owner's own pick), `tcap` the
    per-team lead cap (-1: none), `max_overlap` the most players two entries
    may share (None: no cap).

    A cap is a COUNT of the entries the cover is building toward, and every
    portfolio the board reports is a prefix of one cover (showdown: 5, 10 and
    20), so the count at step n is the cap of the smallest reported size at
    or above n: a 40% cap holds at 2 of 5, 4 of 10 and 8 of 20, every prefix
    the tab can show. greedy_cover calls reset(k) first and appends one
    report per cover to `reports` (one per contest on a classic board)."""

    def __init__(self, members, lead, pcap, tcap, max_overlap=None, sizes=()):
        self.np = np
        self.members = np.asarray(members, dtype=np.int64)
        self.lead = np.asarray(lead, dtype=np.int64)
        self.pcap = np.asarray(pcap, dtype=np.int64)
        self.tcap = np.asarray(tcap, dtype=np.int64)
        self.max_overlap = None if max_overlap is None else int(max_overlap)
        self.sizes_in = tuple(int(s) for s in sizes)
        self.reports = []
        self.reset(max(self.sizes_in or (1,)))

    def reset(self, k):
        np = self.np
        self.k = int(k)
        self.sizes = sorted({s for s in self.sizes_in if 0 < s <= self.k} | {self.k})
        self.cnt = np.zeros(len(self.pcap), dtype=np.int64)
        self.tcnt = np.zeros(len(self.tcap), dtype=np.int64)
        self.maxov = np.zeros(len(self.members), dtype=np.int64)
        self.changed = []               # per pick: did a cap turn the cover away from its uncapped best?
        self.taken = []

    def _size(self, n):
        return next((s for s in self.sizes if s >= n), n)

    def _caps(self, pct, s):
        """cap_count over an array of percents (-1: no cap), at size s."""
        np = self.np
        return np.where(pct < 0, s + 1, np.where(pct == 0, 0, np.maximum(1, (pct * s) // 100)))

    def blocked_by(self, n):
        """{cap: (K,) bool} -- which candidates each cap refuses as the n-th entry."""
        s = self._size(n)
        out = {}
        if (self.pcap >= 0).any():
            out["player"] = (self.cnt[self.members] + 1 > self._caps(self.pcap, s)[self.members]).any(axis=1)
        if (self.tcap >= 0).any():
            out["team"] = self.tcnt[self.lead] + 1 > self._caps(self.tcap, s)[self.lead]
        if self.max_overlap is not None and self.taken:
            out["overlap"] = self.maxov > self.max_overlap
        return out

    def blocked(self, n):
        np = self.np
        bad = np.zeros(len(self.members), dtype=bool)
        for m in self.blocked_by(n).values():
            bad |= m
        return bad

    def take(self, j, changed=False):
        np = self.np
        self.changed.append(bool(changed))
        row = self.members[j]
        np.add.at(self.cnt, row, 1)
        self.tcnt[self.lead[j]] += 1
        ov = np.isin(self.members, row).sum(axis=1)
        self.maxov = np.maximum(self.maxov, ov)
        self.taken.append(int(j))

    def finish(self, k, chosen, stop_n=None):
        """One cover's report: how many it took, whether a cap stopped it short of k,
        and which caps refused candidates at that step."""
        rep = {"k": int(k), "entries": len(chosen), "short": stop_n is not None,
               "caps_changed_picks": int(sum(self.changed))}
        if stop_n is not None:
            np = self.np
            live = np.ones(len(self.members), dtype=bool)
            live[list(chosen)] = False
            by = self.blocked_by(stop_n)
            rep["stopped_by"] = {name: int((m & live).sum()) for name, m in sorted(by.items())}
            rep["candidates"] = int(live.sum())
        self.reports.append(rep)
        return rep


def limits_for(rows, players, port, forced=(), sizes=(), lead_col=0, lead_forced=None):
    """(Limits, notes) for the shortlist `rows` (K, S) of indices into
    `players` (each with name and team), or (None, notes) when `port` caps
    nothing. Forced men are exempt from the player caps (they are in every
    entry by the owner's pick; nfl_dfs.pick_resolve refuses a cap NAMED on one)
    and the team of a forced lead -- `lead_forced`, the forced captain on a
    showdown; a forced quarterback on a classic board when None -- from the
    team cap; an overlap cap below the forced count is lifted to it (every
    entry shares them), each said in `notes`."""
    notes = []
    if not port:
        return None, notes
    forced = set(forced or ())
    names = [p["name"] for p in players]
    teams = sorted({p.get("team") or "" for p in players})
    tcode = {t: i for i, t in enumerate(teams)}
    pcap = [-1] * len(players)
    any_cap = False
    if port.player_pct is not None:
        for i, nm in enumerate(names):
            if nm not in forced:
                pcap[i] = int(port.player_pct)
                any_cap = True
        if forced:
            notes.append(f"the {port.player_pct}% player cap leaves out your forced players: they are in every entry")
    for i, nm in enumerate(names):
        if nm in (port.players or {}) and nm not in forced:
            pcap[i] = int(port.players[nm])
            any_cap = True
    tcap = [-1] * len(teams)
    if port.team_pct is not None:
        word = "captain" if lead_forced is not None else "quarterback"
        if lead_forced is None:
            lead_forced = [nm for nm, p in zip(names, players) if nm in forced and p.get("pos") == "QB"]
        free_of = {(p.get("team") or "") for nm, p in zip(names, players) if nm in set(lead_forced)}
        for t, i in tcode.items():
            if t not in free_of:
                tcap[i] = int(port.team_pct)
                any_cap = True
        for t in sorted(free_of):
            notes.append(f"the {port.team_pct}% team cap leaves out {t}: your forced {word} leads every entry")
    ov = port.max_overlap
    if ov is not None:
        any_cap = True
        n_forced = len(forced & set(names))
        if ov < n_forced:
            notes.append(f"entries share up to {n_forced} players, not {ov}: your {n_forced} forced players are in all of them")
            ov = n_forced
    if not any_cap:
        return None, notes
    lead = [tcode[players[int(r[lead_col])].get("team") or ""] for r in rows]
    return Limits(rows, lead, pcap, tcap, ov, sizes), notes


class SheetCaps:
    """The same caps for the lineup sheet's multi-entry (nfl_dfs.build and
    _build_showdown), which builds its entries one at a time rather than
    covering a shortlist: `out(pool)` names who may not enter the next lineup
    (a player at his cap; a lead -- quarterback, or captain with
    `lead_pos` "CPT" -- of a team at its cap), `fits(names, lead_team,
    chosen)` checks a finished candidate, `take` books one. Counts are of
    the `n` entries asked for (one portfolio, no prefixes). Forced men and a
    forced lead's team are exempt, as on the boards. Pure Python: the sheet
    runs in a web worker."""

    def __init__(self, port, n, forced=(), lead_forced_teams=()):
        self.port, self.n = port, int(n)
        self.forced = set(forced or ())
        self.free_teams = set(lead_forced_teams or ())
        self.cnt, self.tcnt = collections.Counter(), collections.Counter()

    def player_cap(self, nm):
        if nm in self.forced:
            return None
        pct = (self.port.players or {}).get(nm, self.port.player_pct)
        return None if pct is None else cap_count(pct, self.n)

    def team_cap(self, team):
        if self.port.team_pct is None or team in self.free_teams:
            return None
        return cap_count(self.port.team_pct, self.n)

    def _full(self, nm):
        c = self.player_cap(nm)
        return c is not None and self.cnt[nm] + 1 > c

    def _team_full(self, team):
        c = self.team_cap(team)
        return c is not None and self.tcnt[team] + 1 > c

    def out(self, pool, lead_pos="QB"):
        """Names that may not be in the next entry (as a lead only, for a team at its cap)."""
        return {p["name"] for p in pool if self._full(p["name"])
                or (p.get("pos") == lead_pos and self._team_full(p.get("team") or ""))}

    def fits(self, names, lead_team, chosen=(), max_overlap=None):
        if any(self._full(nm) for nm in names) or self._team_full(lead_team or ""):
            return False
        return max_overlap is None or all(len(set(names) & set(c)) <= max_overlap for c in chosen)

    def take(self, names, lead_team):
        self.cnt.update(set(names))
        self.tcnt[lead_team or ""] += 1


# ---- reading a portfolio -------------------------------------------------------
def _lead(row):
    """The team an entry is built around: its captain's on a showdown, its quarterback's on a classic lineup."""
    lu = row.get("lineup") or []
    for slot in ("CPT", "QB"):
        p = next((q for q in lu if q.get("slot") == slot), None)
        if p is not None:
            return p.get("team") or ""
    return ""


def concentration(rows, forced=(), warn_pct=None):
    """How concentrated a portfolio is: every player's exposure (entries
    holding him, by player whatever the slot), every team's lead share (the
    quarterback's team, or the captain's) and players per entry, the largest
    number of players any two entries share and which two, and flags -- a
    player above `warn_pct` (WARN_PCT when None; forced men are listed, never
    flagged: they are in every entry on purpose) and two entries one player
    apart. `rows` are the board's or the sheet's entry rows ({lineup: [{name,
    team, slot}]}). Flags only from three entries up: one or two entries
    always put somebody in all of them. Pure Python; static/dfspick.js
    dfsConcentration is the same definition."""
    forced = set(forced or ())
    warn = WARN_PCT if warn_pct is None else int(warn_pct)
    sets, teams_of, lead_n, per_team = [], {}, collections.Counter(), collections.Counter()
    for r in rows:
        lu = r.get("lineup") or []
        names = []
        for p in lu:
            nm = p.get("name")
            if nm is None or nm in names:
                continue
            names.append(nm)
            teams_of.setdefault(nm, p.get("team") or "")
            per_team[p.get("team") or ""] += 1
        sets.append(frozenset(names))
        lead_n[_lead(r)] += 1
    n = len(sets)
    expo = collections.Counter(nm for s in sets for nm in s)
    size = max((len(s) for s in sets), default=0)
    best, pair = 0, None
    for i in range(n):
        for j in range(i + 1, n):
            o = len(sets[i] & sets[j])
            if o > best:
                best, pair = o, [i + 1, j + 1]
    # half up, in integers: Python's round() goes to even on 6.25 (16 entries) where the tab's JavaScript
    # rounds up, and the two must agree to the digit (a guard runs both)
    pct = lambda c: ((2000 * c + n) // (2 * n)) / 10 if n else 0.0
    players = [{"name": nm, "team": teams_of.get(nm, ""), "n": c, "pct": pct(c), "forced": nm in forced,
                "flag": n >= 3 and nm not in forced and 100 * c > warn * n}
               for nm, c in sorted(expo.items(), key=lambda kv: (-kv[1], kv[0]))]
    teams = [{"team": t, "lead_n": lead_n.get(t, 0), "lead_pct": pct(lead_n.get(t, 0)),
              "players_avg": ((200 * per_team[t] + n) // (2 * n)) / 100 if n else 0.0}
             for t in sorted(set(per_team) | set(lead_n), key=lambda t: (-lead_n.get(t, 0), -per_team[t], t)) if t]
    flags = [f"{p['name']} is in {p['n']} of {n} entries ({p['pct']:g}%), above {warn}%" for p in players if p["flag"]]
    if n >= 3 and size and best >= size - 1:
        flags.append(f"entries {pair[0]} and {pair[1]} share {best} of {size} players")
    # the 30 most held: a twenty-entry classic portfolio names up to 180 players
    return {"entries": n, "size": size, "warn_pct": warn, "players": players[:30], "teams": teams,
            "max_overlap": best, "overlap_pair": pair, "flags": flags}


# ---- the boards' rule masks (OUR entries only) -----------------------------------
_WRTE = ("WR", "TE")


def classic_rules_mask(idx, players, rules):
    """[(rule in words, (rows,) bool)] for the owner's roster rules on classic
    rows (QB, RB, RB, WR, WR, WR, TE, FLEX, DST: dfs_tourney.CL_SLOTS). Each
    mask on its own, in rules_text's order, so a board that ends with no
    candidate names the rule that emptied it. The house rules (classic_allowed)
    are applied beside these, never replaced: the owner's stack can only
    tighten the house's one receiver."""
    if not rules:
        return []
    idx = np.asarray(idx)
    teams = sorted({p.get("team") or "" for p in players} | {p.get("opp") or "" for p in players})
    tc = {t: i for i, t in enumerate(teams)}
    team = np.asarray([tc[p.get("team") or ""] for p in players])
    opp = np.asarray([tc[p.get("opp") or ""] for p in players])
    wrte = np.asarray([p.get("pos") in _WRTE for p in players])
    sal = np.asarray([int(p["salary"]) for p in players])
    words = rules_text(rules)
    out, w = [], iter(words)
    qb_t, qb_o = team[idx[:, 0]], opp[idx[:, 0]]
    catch = idx[:, 3:8]                                    # three receivers, the tight end, the FLEX
    if rules.stack:
        out.append((next(w), ((team[catch] == qb_t[:, None]) & wrte[catch]).sum(axis=1) >= rules.stack))
    if rules.bring:
        has = ((team[catch] == qb_o[:, None]) & wrte[catch]).any(axis=1)
        out.append((next(w), has if rules.bring == "required" else ~has))
    if rules.team_max:
        T = team[idx]
        per = np.max(np.stack([(T == T[:, [k]]).sum(axis=1) for k in range(T.shape[1])], axis=1), axis=1)
        out.append((next(w), per <= rules.team_max))
    if rules.min_salary:
        out.append((next(w), sal[idx].sum(axis=1) >= rules.min_salary))
    return out


def showdown_rules_mask(idx, players, rules):
    """The same for showdown rows (captain first, at his captain's salary):
    the team maximum and the minimum salary (the stack and bring-back rules are
    classic rules; pick_resolve refuses them on a showdown)."""
    if not rules:
        return []
    idx = np.asarray(idx)
    out = []
    if rules.team_max:
        teams = sorted({p.get("team") or "" for p in players})
        tc = {t: i for i, t in enumerate(teams)}
        T = np.asarray([tc[p.get("team") or ""] for p in players])[idx]
        per = np.max(np.stack([(T == T[:, [k]]).sum(axis=1) for k in range(T.shape[1])], axis=1), axis=1)
        out.append((f"at most {rules.team_max} players from one team", per <= rules.team_max))
    if rules.min_salary:
        csal = np.asarray([int(p.get("cpt_salary") or round(1.5 * int(p["salary"]))) for p in players])
        sal = np.asarray([int(p["salary"]) for p in players])
        tot = csal[idx[:, 0]] + sal[idx[:, 1:]].sum(axis=1)
        out.append((f"at least ${rules.min_salary:,} of salary used", tot >= rules.min_salary))
    return out


def apply_masks(allowed, masks):
    """(allowed & every mask, None) or (allowed & every mask, the first rule
    after which nothing was left), reading the masks in order."""
    run = np.asarray(allowed, dtype=bool).copy()
    first = None
    for label, m in masks:
        run &= m
        if first is None and not run.any():
            first = label
    return run, first
