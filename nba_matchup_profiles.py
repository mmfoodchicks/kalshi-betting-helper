"""NBA matchup engine, the profiles: every player's and every defense's
footprint AS OF a date (strictly earlier games), pure Python -- the research
fit (research/nba_matchup_fit.py) and production (nba_matchup) build them
with this one module, so a feature means the same thing in both.

THE BOOK. One chronological walk over the matchup rows (nba_matchup_data):
each player keeps three sums of his counting stats and on-floor counts --
this season's (`cur`), last season's (`prev`, frozen at the roll) and a
recent EWMA (`rec`, half-life REC_HALF of his own games) -- plus minutes
EWMAs and his last game; each team keeps the same three sums of its
OFFENSE (`off`) and of the opponents' offense against it (`def`, what it
concedes), and the observed-over-expected sums its opponents' players
produced against it (`oe`, filled when the walk is handed each game's
expected stats). Snapshots are taken BEFORE a date's games are added, so a
game never sees itself or anything later (guarded).

WHAT A DEFENSE PROFILE IS. The concessions are the observable footprint of
a scheme, never the scheme itself: the shot mix allowed (rim / short /
midrange / three per possession), make rates allowed by zone, how many of
the makes against it are assisted, pull-up and spot-up attempts allowed (the
league's own shot descriptors; spot-up is a proxy for catch-and-shoot),
drives, cuts and putbacks allowed, free throws, offensive rebounds allowed
per chance, early-clock attempts after live-ball starts (the transition
proxy), turnovers forced, pace. Drop / switch / hedge / zone are NOT
labelled anywhere: ESPN's feed cannot see them.

SHRINKAGE. Every ratio is shrunk in log space toward the prior season's
(itself pulled toward the league by a measured persistence), with a K
measured on the fit seasons by split-half reliability
(research/nba_matchup_fit.py measure_team / measure_roles / pass2; the values are in
research/data/nba_matchup/params.json, `team_K` / `role_K` / `oe_K`).

TRACKING_HOOK. The league's own play types (synergy: P&R ball handler, roll
man, isolation, post-up, spot-up, handoff, cut, off-screen, putback,
transition) and tracking (drives, touches, potential assists, contested
rebounds) would replace several proxies here. stats.nba.com does not answer
this host; the owner's PC on a home connection probably can. The optional
hook is `external_profiles` below: a PC-side fetch would store
{season, team: {playtype: {poss, ppp, freq}}, player: {...}} under the deep
key `nba_tracking_<season>`; nothing reads it until a validation on held-out
games says it helps, and it is NOT built (it cannot be tested from here).
"""
import math

import nba_matchup_data as D

REC_HALF = 10.0            # games; the recent EWMA of a player's / team's sums
_REC_D = 0.5 ** (1.0 / REC_HALF)
_MIN_HALVES = (2.0, 6.0, 15.0)      # minutes EWMAs (games)
_MIN_D = tuple(0.5 ** (1.0 / h) for h in _MIN_HALVES)

PSUM = ("g", "gs", "min", "secs", "pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "ast",
        "tov", "stl", "blk", "poss_off", "poss_def", "tm_fga", "tm_fta", "tm_tov", "tm_fgm", "oreb_ch",
        "dreb_ch", "a_rim", "m_rim", "a_short", "m_short", "a_mid", "m_mid", "a_3", "m_3", "ast_m2", "ast_m3",
        "pull_a", "pull_m", "pull3_a", "pull3_m", "spot_a", "spot3_a", "spot3_m", "drive_a", "drive_m",
        "cut_a", "cut_m", "putback_a", "putback_m", "post_a", "post_m", "sc_a", "sc_pts", "tr_a", "tr_pts",
        "ast_rim", "ast_3", "sfd")
PS = {k: i for i, k in enumerate(PSUM)}
_PSRC = tuple(D.PI.get(k, -1) for k in PSUM)
TSUM = ("g",) + D.TF[3:]
TS = {k: i for i, k in enumerate(TSUM)}
_TSRC = tuple(D.TI.get(k, -1) for k in TSUM)

# observed-over-expected: the stats a defense is measured on, by the
# opposing player's position group (0 all, 1 G, 2 F, 3 C)
OE_STATS = ("pts", "reb", "ast", "tpm", "tpa", "oreb", "dreb")
OE_CATS = ("all", "G", "F", "C")
_OE_N = len(OE_STATS) * len(OE_CATS) * 2


def oe_idx(stat, cat):
    return (OE_STATS.index(stat) * len(OE_CATS) + cat) * 2


def _zeros(n):
    return [0.0] * n


class _Player:
    __slots__ = ("season", "cur", "prev", "rec", "recw", "pos", "team", "last_date", "mw", "mx", "stw",
                 "stx", "n_all", "last_tg", "name")

    def __init__(self):
        self.season = None
        self.cur, self.prev, self.rec = _zeros(len(PSUM)), _zeros(len(PSUM)), _zeros(len(PSUM))
        self.recw = 0.0
        self.pos = -1
        self.team = None
        self.last_date = None
        self.mw = [0.0, 0.0, 0.0]
        self.mx = [0.0, 0.0, 0.0]
        self.stw = 0.0
        self.stx = 0.0
        self.n_all = 0
        self.last_tg = -99
        self.name = ""


class _Team:
    __slots__ = ("season", "off", "dfn", "oe", "g", "tg", "last_date")

    def __init__(self):
        self.season = None
        # each: [cur, prev, rec]
        self.off = [_zeros(len(TSUM)), _zeros(len(TSUM)), _zeros(len(TSUM))]
        self.dfn = [_zeros(len(TSUM)), _zeros(len(TSUM)), _zeros(len(TSUM))]
        self.oe = [_zeros(_OE_N), _zeros(_OE_N), _zeros(_OE_N)]
        self.g = 0
        self.tg = 0              # team games played (all seasons), for "missed his team's last games"
        self.last_date = None


def _day(iso):
    """Days since 2000-01-01 of an ISO date/datetime (UTC date of the tip)."""
    import datetime
    d = datetime.date(int(iso[:4]), int(iso[5:7]), int(iso[8:10]))
    return (d - datetime.date(2000, 1, 1)).days


class Book:
    """The as-of state. Feed it dates in order: snapshot every row of a date
    (player(), team(), rotation()), then add_date(entries, expected)."""

    def __init__(self):
        self.players = {}
        self.teams = {}
        self.season = None
        self.lg_off = [_zeros(len(TSUM)), _zeros(len(TSUM))]      # cur, prev
        self.lg_pos = [[_zeros(len(PSUM)) for _ in range(3)], [_zeros(len(PSUM)) for _ in range(3)]]
        self.dates = 0

    # -- season roll ----------------------------------------------------------
    def roll(self, season):
        if self.season == season:
            return
        if self.season is not None and season == self.season + 1:
            self.lg_off = [_zeros(len(TSUM)), self.lg_off[0]]
            self.lg_pos = [[_zeros(len(PSUM)) for _ in range(3)], self.lg_pos[0]]
        elif self.season is not None:
            self.lg_off = [_zeros(len(TSUM)), _zeros(len(TSUM))]
            self.lg_pos = [[_zeros(len(PSUM)) for _ in range(3)], [_zeros(len(PSUM)) for _ in range(3)]]
        self.season = season

    def _p(self, pid):
        p = self.players.get(pid)
        if p is None:
            p = self.players[pid] = _Player()
        if p.season != self.season:
            if p.season is not None and p.season == self.season - 1:
                p.prev = p.cur
            elif p.season is not None:
                p.prev = _zeros(len(PSUM))
            p.cur = _zeros(len(PSUM))
            p.season = self.season
        return p

    def _t(self, ab):
        t = self.teams.get(ab)
        if t is None:
            t = self.teams[ab] = _Team()
        if t.season != self.season:
            keep = t.season is not None and t.season == self.season - 1
            for arr in (t.off, t.dfn, t.oe):
                arr[1] = arr[0] if keep else _zeros(len(arr[0]))
                arr[0] = _zeros(len(arr[0]))
            t.g = 0
            t.season = self.season
        return t

    # -- snapshots (as of BEFORE the current date) --------------------------
    def player(self, pid, date_iso):
        """A copy of the player's state for a game on `date_iso`, or None when
        he has never played."""
        p = self.players.get(pid)
        if p is None or p.n_all == 0:
            return None
        p = self._p(pid)
        days = _day(date_iso) - p.last_date if p.last_date is not None else 99
        mins = [p.mx[i] / p.mw[i] if p.mw[i] else 0.0 for i in range(3)]
        return {"cur": list(p.cur), "prev": list(p.prev), "rec": list(p.rec), "recw": p.recw, "pos": p.pos,
                "team": p.team, "days": days, "m_e": mins, "st_e": p.stx / p.stw if p.stw else 0.0,
                "n_all": p.n_all, "last_tg": p.last_tg, "name": p.name,
                "lg_pos": list(self.lg_pos[1][p.pos]) if p.pos >= 0 else None,
                "lg_pos_cur": list(self.lg_pos[0][p.pos]) if p.pos >= 0 else None}

    def team(self, ab):
        t = self._t(ab)
        return {"off": [list(x) for x in t.off], "def": [list(x) for x in t.dfn], "oe": [list(x) for x in t.oe],
                "g": t.g, "tg": t.tg, "lg": [list(self.lg_off[0]), list(self.lg_off[1])]}

    def rotation(self, ab, min_e6=15.0, within=3):
        """Players whose last team is `ab`, averaging min_e6+ minutes on his
        six-game EWMA, who played in one of the team's last `within` games:
        the regulars whose absence a game would show."""
        t = self.teams.get(ab)
        if t is None:
            return []
        out = []
        for pid, p in self.players.items():
            if p.team == ab and t.tg - p.last_tg <= within and p.mw[1] and p.mx[1] / p.mw[1] >= min_e6:
                out.append(pid)
        return out

    # -- adding a date's games ---------------------------------------------
    def add_date(self, entries, expected=None):
        """Every game of one date. `expected` {gid: {pid: {stat: E}}} fills
        the defenses' observed-over-expected sums (pass 2 of the fit)."""
        expected = expected or {}
        for e in entries:
            day = _day(e["date"])
            sides = (e["home"], e["away"])
            T = [self._t(ab) for ab in sides]
            for s in (0, 1):
                own, opp = e["teams"][s], e["teams"][1 - s]
                t = T[s]
                for k, src in enumerate(_TSRC):
                    if src < 0:
                        continue
                    v, w = own[src], opp[src]
                    t.off[0][k] += v
                    t.off[2][k] = t.off[2][k] * _REC_D + v
                    t.dfn[0][k] += w
                    t.dfn[2][k] = t.dfn[2][k] * _REC_D + w
                    self.lg_off[0][k] += v
                for arr in (t.off, t.dfn):
                    arr[0][0] += 1
                    arr[2][0] = arr[2][0] * _REC_D + 1
                self.lg_off[0][0] += 1
                t.g += 1
                t.tg += 1
                t.last_date = day
                # observed-over-expected for this defense: the OTHER side's players
                ex = expected.get(e["id"]) or {}
                oe_new = _zeros(_OE_N)
                for pid, r in e["players"].items():
                    if r[D.PI["side"]] == s or not r[D.PI["played"]]:
                        continue
                    xp = ex.get(pid)
                    if not xp:
                        continue
                    act = {"pts": r[D.PI["pts"]], "reb": r[D.PI["oreb"]] + r[D.PI["dreb"]], "ast": r[D.PI["ast"]],
                           "tpm": r[D.PI["tpm"]], "tpa": r[D.PI["tpa"]], "oreb": r[D.PI["oreb"]],
                           "dreb": r[D.PI["dreb"]]}
                    g = r[D.PI["pos_g"]]
                    for st in OE_STATS:
                        if st not in xp:
                            continue
                        for c in ((0, g + 1) if g >= 0 else (0,)):
                            j = oe_idx(st, c)
                            oe_new[j] += act[st]
                            oe_new[j + 1] += xp[st]
                tdef = T[s]              # side s's defense is what these players faced
                for j in range(_OE_N):
                    tdef.oe[0][j] += oe_new[j]
                    tdef.oe[2][j] = tdef.oe[2][j] * _REC_D + oe_new[j]
            for pid, r in e["players"].items():
                if not r[D.PI["played"]]:
                    continue
                p = self._p(pid)
                g = r[D.PI["pos_g"]]
                if g >= 0:
                    p.pos = g
                p.team = sides[r[D.PI["side"]]]
                p.name = (e.get("names") or {}).get(pid, p.name)
                vals = [0.0] * len(PSUM)
                for k, src in enumerate(_PSRC):
                    if src >= 0:
                        vals[k] = r[src]
                vals[PS["g"]] = 1.0
                vals[PS["gs"]] = 1.0 if r[D.PI["starter"]] else 0.0
                for k in range(len(PSUM)):
                    p.cur[k] += vals[k]
                    p.rec[k] = p.rec[k] * _REC_D + vals[k]
                p.recw = p.recw * _REC_D + 1.0
                if p.pos >= 0:
                    lp = self.lg_pos[0][p.pos]
                    for k in range(len(PSUM)):
                        lp[k] += vals[k]
                m = float(r[D.PI["min"]])
                for i, dd in enumerate(_MIN_D):
                    p.mw[i] = p.mw[i] * dd + 1.0
                    p.mx[i] = p.mx[i] * dd + m
                p.stw = p.stw * _MIN_D[1] + 1.0
                p.stx = p.stx * _MIN_D[1] + vals[PS["gs"]]
                p.n_all += 1
                p.last_date = day
                p.last_tg = self.teams[p.team].tg
        self.dates += 1


# ---- features from snapshots -----------------------------------------------
def _comb(snap, k, a, b):
    """cur + a * prev + b * rec of one PSUM field."""
    i = PS[k]
    return snap["cur"][i] + a * snap["prev"][i] + b * snap["rec"][i]


RATE_STATS = ("pts", "reb", "ast", "tpm", "tpa", "oreb", "dreb", "fga", "fta", "stl", "blk", "tov")


def _stat_val(arr, stat):
    if stat == "reb":
        return arr[PS["oreb"]] + arr[PS["dreb"]]
    return arr[PS[stat]]


def player_rate(snap, stat, W):
    """The player's per-minute rate of `stat`: his weighted sums
    (cur + a prev + b rec) shrunk toward his position's league rate with K
    minutes. W[stat] = (a, b, K) fitted on the fit seasons. Returns (rate,
    effective minutes, the share of the estimate that is his own)."""
    a, b, K = W[stat]
    num = _stat_val(snap["cur"], stat) + a * _stat_val(snap["prev"], stat) + b * _stat_val(snap["rec"], stat)
    den = snap["cur"][PS["min"]] + a * snap["prev"][PS["min"]] + b * snap["rec"][PS["min"]]
    lp = snap.get("lg_pos") or snap.get("lg_pos_cur")
    if lp and lp[PS["min"]] > 0:
        prior = _stat_val(lp, stat) / lp[PS["min"]]
    else:
        prior = LEAGUE_PER_MIN.get(stat, 0.0)
    rate = (num + K * prior) / (den + K) if den + K > 0 else prior
    return rate, den, den / (den + K) if den + K > 0 else 0.0


# league per-minute rates (2022-23 .. 2023-24, all players), the fallback prior
# when no position-group league is available (a season's first night)
LEAGUE_PER_MIN = {"pts": 0.4710, "reb": 0.1849, "ast": 0.1074, "tpm": 0.0532, "tpa": 0.1471, "oreb": 0.0442,
                  "dreb": 0.1407, "fga": 0.3698, "fta": 0.0943, "stl": 0.0313, "blk": 0.0219, "tov": 0.0596}


def on_floor_pace(snap, a=0.5, b=1.0):
    """Possessions per 48 minutes while he is on the floor (both ends' average)."""
    secs = _comb(snap, "secs", a, b)
    poss = (_comb(snap, "poss_off", a, b) + _comb(snap, "poss_def", a, b)) / 2.0
    return poss / secs * 2880.0 if secs > 0 else None


# Role features: (name, numerator(s), denominator(s), kind, basis). Each is a
# share or a rate from his own counts, combined cur + 0.5 prev + 1.0 rec and
# shrunk toward his position group's league value with role_K (measured).
ROLE_DEFS = (
    ("usage", ("fga", "fta*0.44", "tov"), ("tm_fga", "tm_fta*0.44", "tm_tov"), "observed",
     "his shots + 0.44 FTA + turnovers over his team's while he is on the floor (USG% proxy from ESPN's box and stints)"),
    ("rim_share", ("a_rim",), ("a_rim", "a_short", "a_mid", "a_3"), "observed", "FGA within 3 ft / FGA"),
    ("short_share", ("a_short",), ("a_rim", "a_short", "a_mid", "a_3"), "observed", "FGA 4-13 ft / FGA"),
    ("mid_share", ("a_mid",), ("a_rim", "a_short", "a_mid", "a_3"), "observed", "two-point FGA 14+ ft / FGA"),
    ("three_share", ("a_3",), ("a_rim", "a_short", "a_mid", "a_3"), "observed", "3PA / FGA"),
    ("ft_rate", ("fta",), ("fga",), "observed", "FTA / FGA"),
    ("assisted_share", ("ast_m2", "ast_m3"), ("fgm",), "observed", "assisted makes / makes (ESPN assist flag)"),
    ("pullup_share", ("pull_a",), ("a_rim", "a_short", "a_mid", "a_3"), "observed",
     "pull-up / step-back / fade / turnaround jumpers / FGA (the league's shot descriptors)"),
    ("pull3_share", ("pull3_a",), ("a_3",), "observed", "pull-up-type threes / 3PA"),
    ("spot3_share", ("spot3_a",), ("a_3",), "proxy",
     "plain 'Jump Shot' threes (no dribble/move word) / 3PA -- catch-and-shoot proxy"),
    ("assisted3_share", ("ast_m3",), ("m_3",), "observed", "assisted 3PM / 3PM"),
    ("drive_share", ("drive_a",), ("a_rim", "a_short", "a_mid", "a_3"), "observed", "'Driving' shots inside 14 ft / FGA"),
    ("cut_share", ("cut_a",), ("a_rim", "a_short", "a_mid", "a_3"), "observed",
     "cutting + alley-oop shots / FGA (rim-runner / roll-finisher proxy)"),
    ("putback_share", ("putback_a",), ("a_rim", "a_short", "a_mid", "a_3"), "observed", "tips + putbacks / FGA"),
    ("post_share", ("post_a",), ("a_rim", "a_short", "a_mid", "a_3"), "proxy",
     "hooks and turnaround shots inside 14 ft / FGA (post-up proxy)"),
    ("second_chance_share", ("sc_a",), ("a_rim", "a_short", "a_mid", "a_3"), "observed",
     "FGA after his team's offensive rebound / FGA"),
    ("transition_share", ("tr_a",), ("a_rim", "a_short", "a_mid", "a_3"), "proxy",
     "first shots within 7 s of a defensive rebound or steal / FGA (early-clock proxy, no tracking)"),
    ("oreb_pct", ("oreb",), ("oreb_ch",), "observed", "OREB / rebound chances on his team's misses while on the floor"),
    ("dreb_pct", ("dreb",), ("dreb_ch",), "observed", "DREB / rebound chances on opponents' misses while on the floor"),
    ("ast_pct", ("ast",), ("tm_fgm", "-fgm"), "observed", "assists / teammates' made FG while on the floor"),
    ("ast3_share", ("ast_3",), ("ast",), "observed", "assists on threes / assists"),
    ("ast_rim_share", ("ast_rim",), ("ast",), "observed", "assists on rim makes / assists"),
    ("tov_rate", ("tov",), ("fga", "fta*0.44", "tov"), "observed", "turnovers / (FGA + 0.44 FTA + TOV)"),
    ("min_share", ("min",), ("g*48",), "observed", "minutes / 48 per game played"),
)
ROLE_NAMES = tuple(r[0] for r in ROLE_DEFS)
_ROLE_A, _ROLE_B = 0.5, 1.0


def _expr(arr, terms):
    v = 0.0
    for t in terms:
        sign = 1.0
        if t.startswith("-"):
            sign, t = -1.0, t[1:]
        mult = 1.0
        if "*" in t:
            t, m = t.split("*")
            mult = float(m)
        v += sign * mult * arr[PS[t]]
    return v


def _combined(snap):
    a, b = _ROLE_A, _ROLE_B
    return [snap["cur"][i] + a * snap["prev"][i] + b * snap["rec"][i] for i in range(len(PSUM))]


def role(snap, role_K=None, pos_means=None):
    """{feature: {"value", "raw", "n", "kind", "basis"}} -- his style as
    continuous shares, each shrunk toward his position group's league value
    with K denominators (role_K, measured). `n` is the denominator he has."""
    role_K = role_K or {}
    arr = _combined(snap)
    lp = snap.get("lg_pos") or snap.get("lg_pos_cur")
    out = {}
    for name, num, den, kind, basis in ROLE_DEFS:
        n = _expr(arr, den)
        x = _expr(arr, num)
        prior = None
        if pos_means and name in pos_means:
            prior = pos_means[name]
        elif lp:
            dn = _expr(lp, den)
            prior = _expr(lp, num) / dn if dn > 0 else None
        K = role_K.get(name, 20.0)
        if prior is None:
            prior = x / n if n > 0 else 0.0
        val = (x + K * prior) / (n + K) if n + K > 0 else prior
        out[name] = {"value": val, "raw": (x / n if n > 0 else None), "n": round(n, 1), "kind": kind, "basis": basis}
    return out


# Team concession channels: (name, numerator, denominator, kind, basis). The
# per-possession channels divide by the opponents' possessions.
CONC_DEFS = (
    ("pts", "pts", "poss", "observed", "points allowed per possession"),
    ("rim", "a_rim", "poss", "observed", "rim FGA (0-3 ft) allowed per possession"),
    ("short", "a_short", "poss", "observed", "4-13 ft FGA allowed per possession"),
    ("mid", "a_mid", "poss", "observed", "14 ft+ two-point FGA allowed per possession"),
    ("three", "a_3", "poss", "observed", "3PA allowed per possession"),
    ("rim_pct", "m_rim", "a_rim", "observed", "FG% allowed at the rim"),
    ("short_pct", "m_short", "a_short", "observed", "FG% allowed 4-13 ft"),
    ("mid_pct", "m_mid", "a_mid", "observed", "FG% allowed 14 ft+ (twos)"),
    ("three_pct", "m_3", "a_3", "observed", "3P% allowed"),
    ("fgm", "fgm", "poss", "observed", "made FG allowed per possession"),
    ("miss", "miss", "poss", "observed", "missed FG forced per possession"),
    ("ast_share", "ast_m", "fgm", "observed", "share of made FG allowed that were assisted"),
    ("ast3", "ast_m3", "poss", "observed", "assisted 3PM allowed per possession"),
    ("unast3", "unast_m3", "poss", "observed", "unassisted 3PM allowed per possession"),
    ("unast2", "unast_m2", "poss", "observed", "unassisted 2PM allowed per possession"),
    ("pull", "pull_a", "poss", "observed", "pull-up-type jumpers allowed per possession (league descriptors)"),
    ("spot3", "spot3_a", "poss", "proxy", "plain-jumper threes allowed per possession (catch-and-shoot proxy)"),
    ("drive", "drive_a", "poss", "observed", "driving shots allowed per possession"),
    ("cut", "cut_a", "poss", "observed", "cutting / alley-oop shots allowed per possession"),
    ("putback", "putback_a", "poss", "observed", "tips / putbacks allowed per possession"),
    ("post", "post_a", "poss", "proxy", "hooks / turnaround shots inside 14 ft allowed per possession"),
    ("fta", "fta", "poss", "observed", "free throws allowed per possession"),
    ("tov", "tov", "poss", "observed", "turnovers forced per possession"),
    ("steal", "tov_live", "poss", "observed", "live-ball turnovers (steals) forced per possession"),
    ("oreb", "oreb", "oreb_ch", "observed", "offensive rebounds allowed per rebound chance"),
    ("early", "tr_a", "live_starts", "proxy", "early-clock first shots allowed per live-ball start (transition proxy)"),
    ("second", "sc_a", "poss", "observed", "second-chance FGA allowed per possession"),
    ("pace", "poss", "secs", "observed", "possessions per 48 minutes in its games"),
)
CONC_NAMES = tuple(c[0] for c in CONC_DEFS)


def _rate(arr, num, den):
    d = arr[TS[den]]
    if den == "secs":
        return (arr[TS[num]] / d * 2880.0) if d > 0 else None, d / 2880.0 * 100.0
    return (arr[TS[num]] / d) if d > 0 else None, d


def concessions(tsnap, side="def", team_K=None, rho=None):
    """{channel: {"ratio", "raw_ratio", "n", "kind", "basis", "recent_ratio"}} --
    the team's rate in each channel over the league's, this season's raw ratio
    shrunk in log space toward last season's (pulled toward 1 by the measured
    persistence rho) with K denominators (team_K). `side` "def" is what the
    team concedes; "off" its own offense."""
    team_K = team_K or {}
    rho = rho or {}
    S = tsnap[side]
    lg_cur, lg_prev = tsnap["lg"]
    # the league reference: this season's totals, last season's added at a
    # quarter weight (stable on opening week, current by midseason)
    lg = [lg_cur[i] + 0.25 * lg_prev[i] for i in range(len(TSUM))]
    out = {}
    for name, num, den, kind, basis in CONC_DEFS:
        lr, _ = _rate(lg, num, den)
        lp, _ = _rate(lg_prev, num, den)
        cur, n = _rate(S[0], num, den)
        prev, n_prev = _rate(S[1], num, den)
        rec, _ = _rate(S[2], num, den)
        if not lr:
            out[name] = {"ratio": 1.0, "raw_ratio": None, "n": 0, "kind": kind, "basis": basis, "recent_ratio": None}
            continue
        K = team_K.get(f"{side}:{name}", team_K.get(name, 400.0))
        r_ = rho.get(f"{side}:{name}", rho.get(name, 0.5))
        lprior = 0.0
        if prev and lp and n_prev > 0:
            wp = n_prev / (n_prev + K)
            lprior = r_ * wp * math.log(max(1e-6, prev / lp))
        w = n / (n + K) if n > 0 else 0.0
        lcur = math.log(max(1e-6, cur / lr)) if cur else 0.0
        val = math.exp(w * lcur + (1.0 - w) * lprior)
        rr = None
        if rec:
            nr = S[2][TS[den]]
            wr = nr / (nr + K)
            rr = math.exp(wr * math.log(max(1e-6, rec / lr)) + (1 - wr) * math.log(val))
        out[name] = {"ratio": val, "raw_ratio": (cur / lr if cur else None), "n": round(n, 1), "kind": kind,
                     "basis": basis, "recent_ratio": rr, "league": lr}
    return out


def oe(tsnap, stat, cat, oe_K=None, rho=0.5):
    """The defense's observed-over-expected for `stat` against opponents of
    position group `cat` (0 all, 1 G, 2 F, 3 C): what those players produced
    against it over what their own as-of rates said, this season shrunk with
    K expected-units toward last season's (times the persistence rho).
    Returns (ratio, expected units this season, recent ratio)."""
    oe_K = oe_K or {}
    j = oe_idx(stat, cat)
    K = oe_K.get(f"{stat}:{OE_CATS[cat]}", oe_K.get(stat, 50.0))
    cur, prev, rec = tsnap["oe"]
    lprior = 0.0
    if prev[j + 1] > 0 and prev[j] > 0:
        wp = prev[j + 1] / (prev[j + 1] + K)
        lprior = rho * wp * math.log(prev[j] / prev[j + 1])
    n = cur[j + 1]
    w = n / (n + K) if n > 0 else 0.0
    lc = math.log(max(1e-6, cur[j] / n)) if n > 0 and cur[j] > 0 else 0.0
    val = math.exp(w * lc + (1 - w) * lprior)
    rr = None
    if rec[j + 1] > 0 and rec[j] > 0:
        wr = rec[j + 1] / (rec[j + 1] + K)
        rr = math.exp(wr * math.log(rec[j] / rec[j + 1]) + (1 - wr) * math.log(val))
    return val, n, rr


def minutes_features(snap):
    """The minutes model's inputs from the player's own history: EWMAs at
    half-lives 2 / 6 / 15 games, this season's and last season's average,
    his recent starter rate, rest (back-to-back), games this season."""
    cur, prev = snap["cur"], snap["prev"]
    g_cur, g_prev = cur[PS["g"]], prev[PS["g"]]
    avg_cur = cur[PS["min"]] / g_cur if g_cur else None
    avg_prev = prev[PS["min"]] / g_prev if g_prev else None
    e2, e6, e15 = snap["m_e"]
    return {"e2": e2, "e6": e6, "e15": e15, "avg_cur": avg_cur if avg_cur is not None else e15,
            "avg_prev": avg_prev if avg_prev is not None else e15, "has_prev": 1.0 if g_prev else 0.0,
            "st_e": snap["st_e"], "b2b": 1.0 if snap["days"] == 1 else 0.0,
            "long_rest": 1.0 if snap["days"] >= 6 else 0.0, "g_cur": g_cur}


def external_profiles(season):
    """The optional tracking hook (see the module docstring): the PC-side
    play-type / tracking store for `season`, or None. Not built; reading it
    returns None until a fetch exists AND a held-out test has passed."""
    return None
