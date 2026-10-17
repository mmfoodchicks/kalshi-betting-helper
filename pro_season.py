"""NBA and NHL standings, tiebreakers and playoff formats, and the season
Monte Carlo that plays the rest of a season through them.

The rules (2025-26, both leagues; checked against the real final standings
of 2023-24, 2024-25 and 2025-26 by research/pro_format_check.py, which
replays every real result through this module and requires ESPN's own
final conference order):

NHL
  Points: 2 a win, 1 an overtime or shootout loss. Ties in points broken by
  regulation wins (RW), regulation + overtime wins (ROW), total wins, points
  in the games between the tied clubs (two clubs whose series was uneven
  drop the first game in the city that hosted the extra one; three or more
  use points percentage among them), goal differential (a shootout win
  credits one goal, as the standings do), goals for, then the draw.
  Playoffs: the top three of each division, then the conference's next two
  as wildcards. The division winner with the better record meets the
  second wildcard, the other division winner the first; second meets third
  in each division; the bracket is fixed through the conference final.
  Every series is best of seven, 2-2-1-1-1, home ice to the better
  regular-season record (not the seed). Playoff overtime is played to a
  goal -- no shootout.

NBA
  Two clubs tied: head-to-head, a division winner over a non-winner,
  division record (same division only), conference record, record against
  the playoff-eligible clubs of their own conference, then of the other
  conference, then point differential, then the draw. Three or more:
  division winner, record in the games among them, division record (all in
  one division), conference record, record against own-conference playoff
  eligibles, point differential; whenever a criterion separates any club,
  the rest restart from the top -- two-club rules if two remain. "Playoff
  eligible" is the conference's top ten by record, ties at tenth included.
  Seeds 1-6 go straight in; 7 hosts 8 (the winner is the 7 seed), 9 hosts 10
  (the loser is out), the 7-8 loser hosts the 9-10 winner for the 8 seed.
  1v8 and 4v5 meet in round two, 2v7 and 3v6 likewise; best of seven,
  2-2-1-1-1, home court to the better record.

The season Monte Carlo takes, per (home, away) pair, the probability the
BOARD's validated game model gives -- NHL: home win in regulation, home win
in OT/SO, away win in OT/SO (the rest the away side in regulation) and the
share of OT/SO decided in the shootout; NBA: home win. Games already played
are banked at their real results. Pure Python: the server has no numpy.
"""
import math
import random
from collections import defaultdict

# ---- the standings table ------------------------------------------------------


class Table:
    """Every club's running record for one season (real games banked, then
    one simulated season added on a copy)."""

    __slots__ = ("league", "conf", "div", "w", "l", "otl", "rw", "row", "gf", "ga", "pts",
                 "cw", "cl", "dw", "dl", "h2h", "pairs", "n")

    def __init__(self, league, conf, div):
        self.league = league
        self.conf = dict(conf)          # abbr -> conference
        self.div = dict(div)            # abbr -> division
        teams = list(conf)
        z = dict.fromkeys(teams, 0)
        self.w, self.l, self.otl, self.rw, self.row = dict(z), dict(z), dict(z), dict(z), dict(z)
        self.gf, self.ga, self.pts = dict(z), dict(z), dict(z)
        self.cw, self.cl, self.dw, self.dl = dict(z), dict(z), dict(z), dict(z)
        self.h2h = defaultdict(lambda: [0, 0, 0])      # (a, b) -> [a wins, a points, games]
        self.pairs = defaultdict(list)                 # (lo, hi) -> [(order, home, pts_home, pts_away)]
        self.n = 0

    def copy(self):
        t = Table.__new__(Table)
        t.league, t.conf, t.div = self.league, self.conf, self.div
        for k in ("w", "l", "otl", "rw", "row", "gf", "ga", "pts", "cw", "cl", "dw", "dl"):
            setattr(t, k, dict(getattr(self, k)))
        t.h2h = defaultdict(lambda: [0, 0, 0], {k: list(v) for k, v in self.h2h.items()})
        t.pairs = defaultdict(list, {k: list(v) for k, v in self.pairs.items()})
        t.n = self.n
        return t

    def add(self, home, away, hs, as_, kind="REG"):
        """One finished game. NHL `kind` REG / OT / SO (a shootout's score
        already carries the winner's credited goal)."""
        if home not in self.conf or away not in self.conf:
            return
        hw = hs > as_
        win, lose = (home, away) if hw else (away, home)
        self.w[win] += 1
        self.gf[home] += hs; self.ga[home] += as_
        self.gf[away] += as_; self.ga[away] += hs
        if self.league == "nhl":
            self.pts[win] += 2
            if kind == "REG":
                self.rw[win] += 1
                self.row[win] += 1
                self.l[lose] += 1
            else:
                if kind == "OT":
                    self.row[win] += 1
                self.otl[lose] += 1
                self.pts[lose] += 1
        else:
            self.l[lose] += 1
            self.pts[win] += 1
        if self.conf[home] == self.conf[away]:
            self.cw[win] += 1; self.cl[lose] += 1
            if self.div[home] == self.div[away]:
                self.dw[win] += 1; self.dl[lose] += 1
        lp = 1 if (self.league == "nhl" and kind != "REG") else 0     # an OT/SO loser's point
        hw_pts, aw_pts = (2, lp) if hw else (lp, 2)
        for me, opp, won, pts in ((home, away, hw, hw_pts), (away, home, not hw, aw_pts)):
            r = self.h2h[(me, opp)]
            r[0] += 1 if won else 0
            r[1] += pts
            r[2] += 1
        self.pairs[(min(home, away), max(home, away))].append((self.n, home, hw_pts, aw_pts))
        self.n += 1

    def gp(self, t):
        return self.w[t] + self.l[t] + self.otl[t]

    def pct(self, t):
        g = self.gp(t)
        if self.league == "nhl":
            return self.pts[t] / (2.0 * g) if g else 0.0
        return self.w[t] / g if g else 0.0


def _wpct(w, l):
    return w / (w + l) if (w + l) else 0.5


# ---- NHL ordering ---------------------------------------------------------------
def _nhl_h2h_two(T, a, b):
    """Points each of two clubs earned against the other, the uneven series'
    first game in the city with the extra home game left out."""
    games = sorted(T.pairs.get((min(a, b), max(a, b))) or [])
    if len(games) % 2 == 1:
        hosted = defaultdict(int)
        for _o, h, _ph, _pa in games:
            hosted[h] += 1
        extra = max(hosted, key=lambda k: hosted[k])
        for i, (_o, h, _ph, _pa) in enumerate(games):
            if h == extra:
                games = games[:i] + games[i + 1:]
                break
    pa_ = pb = 0
    for _o, h, ph, pa in games:
        if h == a:
            pa_ += ph; pb += pa
        else:
            pa_ += pa; pb += ph
    return pa_, pb


def _nhl_group_h2h(T, group):
    """Points percentage of each club in the games among `group`."""
    out = {}
    for t in group:
        pts = g = 0
        for o in group:
            if o != t:
                r = T.h2h.get((t, o))
                if r:
                    pts += r[1]; g += r[2]
        out[t] = pts / (2.0 * g) if g else 0.5
    return out


def nhl_order(T, teams, rng):
    """`teams` best to worst under the NHL's tiebreakers."""
    def key_basic(t):
        return (T.pct(t), T.pts[t], T.rw[t], T.row[t], T.w[t])
    return _sorted_with(teams, key_basic, lambda grp: _nhl_break(T, grp, rng))


def _nhl_break(T, grp, rng):
    """Order a group tied on points %, points, RW, ROW and wins."""
    if len(grp) == 2:
        a, b = grp
        pa_, pb = _nhl_h2h_two(T, a, b)
        if pa_ != pb:
            return [a, b] if pa_ > pb else [b, a]
        h = {a: 0.0, b: 0.0}
    else:
        h = _nhl_group_h2h(T, grp)
    return _sorted_with(grp, lambda t: (h[t], T.gf[t] - T.ga[t], T.gf[t]),
                        lambda g2: sorted(g2, key=lambda _t: rng.random()))


def _sorted_with(teams, key, breaker):
    """Sort descending by key; each run of equal keys ordered by breaker."""
    s = sorted(teams, key=key, reverse=True)
    out, i = [], 0
    while i < len(s):
        j = i + 1
        while j < len(s) and key(s[j]) == key(s[i]):
            j += 1
        grp = s[i:j]
        out.extend(grp if len(grp) == 1 else breaker(grp))
        i = j
    return out


def nhl_field(T, rng):
    """{conf: {"div": {div: [1st, 2nd, 3rd]}, "wc": [wc1, wc2], "order": the
    conference best to worst, "bracket": [[(hi_a, lo_a), (d2, d3)] per
    division side]}}: the sixteen clubs and the first-round pairs."""
    out = {}
    confs = sorted(set(T.conf.values()))
    for c in confs:
        members = [t for t in T.conf if T.conf[t] == c]
        order = nhl_order(T, members, rng)
        divs = sorted(set(T.div[t] for t in members))
        top = {d: [t for t in order if T.div[t] == d][:3] for d in divs}
        inn = set(x for v in top.values() for x in v)
        wc = [t for t in order if t not in inn][:2]
        # the division winner with the better record draws the second wildcard
        winners = sorted((top[d][0] for d in divs), key=order.index)
        sides = []
        for i, w in enumerate(winners):
            d = T.div[w]
            sides.append({"div": d, "pairs": [(w, wc[1] if i == 0 else wc[0]), (top[d][1], top[d][2])]})
        out[c] = {"div": top, "wc": wc, "order": order, "sides": sides}
    return out


# ---- NBA ordering ---------------------------------------------------------------
def _nba_wpct_among(T, t, group):
    w = l = 0
    for o in group:
        if o != t:
            r = T.h2h.get((t, o))
            if r:
                w += r[0]; l += r[2] - r[0]
    return _wpct(w, l)


def _nba_eligible(T, members):
    """The conference's top ten by record, ties at tenth included."""
    s = sorted(members, key=T.pct, reverse=True)
    if len(s) <= 10:
        return set(s)
    cut = T.pct(s[9])
    return set(t for t in s if T.pct(t) >= cut)


def _nba_vs(T, t, group):
    w = l = 0
    for o in group:
        if o != t:
            r = T.h2h.get((t, o))
            if r:
                w += r[0]; l += r[2] - r[0]
    return _wpct(w, l)


def nba_order(T, teams, rng, div_winners=frozenset(), elig=None, other_elig=None):
    """`teams` best to worst under the NBA's tiebreakers. `div_winners` the
    clubs that won their divisions (empty when ordering one division);
    `elig` / `other_elig` the playoff-eligible clubs of the clubs' own and
    the other conference."""
    elig = elig or set()
    other_elig = other_elig or set()
    return _sorted_with(teams, T.pct, lambda grp: _nba_break(T, grp, rng, div_winners, elig, other_elig))


def _nba_break(T, grp, rng, dw, elig, oelig):
    def recurse(sub):
        return sub if len(sub) == 1 else _nba_break(T, sub, rng, dw, elig, oelig)
    same_div = len(set(T.div[t] for t in grp)) == 1
    pd = {t: T.gf[t] - T.ga[t] for t in grp}
    if len(grp) == 2:
        a, b = grp
        crits = [lambda t: _nba_wpct_among(T, t, grp),
                 lambda t: 1 if t in dw else 0,
                 (lambda t: _wpct(T.dw[t], T.dl[t])) if same_div else None,
                 lambda t: _wpct(T.cw[t], T.cl[t]),
                 lambda t: _nba_vs(T, t, elig),
                 lambda t: _nba_vs(T, t, oelig),
                 lambda t: pd[t]]
        for c in crits:
            if c is None:
                continue
            va, vb = c(a), c(b)
            if va != vb:
                return [a, b] if va > vb else [b, a]
        return [a, b] if rng.random() < 0.5 else [b, a]
    crits = [lambda t: 1 if t in dw else 0,
             lambda t: _nba_wpct_among(T, t, grp),
             (lambda t: _wpct(T.dw[t], T.dl[t])) if same_div else None,
             lambda t: _wpct(T.cw[t], T.cl[t]),
             lambda t: _nba_vs(T, t, elig),
             lambda t: pd[t]]
    for c in crits:
        if c is None:
            continue
        vals = {t: c(t) for t in grp}
        if len(set(vals.values())) > 1:
            # any separation restarts the remaining ties from the top
            s = sorted(grp, key=lambda t: vals[t], reverse=True)
            out, i = [], 0
            while i < len(s):
                j = i + 1
                while j < len(s) and vals[s[j]] == vals[s[i]]:
                    j += 1
                out.extend(recurse(s[i:j]))
                i = j
            return out
    return sorted(grp, key=lambda _t: rng.random())


def nba_field(T, rng):
    """{conf: {"order": 1-15, "div_winners": set, "seeds": the conference's
    order after tiebreaks}} -- seeds 1-6 in, 7-10 the play-in."""
    out = {}
    confs = sorted(set(T.conf.values()))
    elig_by = {c: _nba_eligible(T, [t for t in T.conf if T.conf[t] == c]) for c in confs}
    for c in confs:
        members = [t for t in T.conf if T.conf[t] == c]
        dws = set()
        for d in sorted(set(T.div[t] for t in members)):
            dm = [t for t in members if T.div[t] == d]
            dws.add(nba_order(T, dm, rng, frozenset(), elig_by[c],
                              set().union(*[v for k, v in elig_by.items() if k != c]))[0])
        order = nba_order(T, members, rng, frozenset(dws), elig_by[c],
                          set().union(*[v for k, v in elig_by.items() if k != c]))
        out[c] = {"order": order, "div_winners": dws}
    return out


def league_order(T, rng):
    """Every club best to worst on the league table (home ice / court in a
    series is the better record; the Presidents' Trophy / the best record)."""
    teams = list(T.conf)
    if T.league == "nhl":
        return nhl_order(T, teams, rng)
    return _sorted_with(teams, T.pct, lambda grp: sorted(grp, key=lambda _t: rng.random()))


# ---- games and series --------------------------------------------------------------
_NHL_MARGIN = ((1, 0.42), (2, 0.31), (3, 0.16), (4, 0.07), (5, 0.04))   # regulation winning margins
_CUM = []
_acc = 0.0
for _m, _p in _NHL_MARGIN:
    _acc += _p
    _CUM.append((_acc, _m))


def _nhl_margin(rng):
    u = rng.random() * _acc
    for c, m in _CUM:
        if u <= c:
            return m
    return 1


def play_nhl(probs, home, away, rng, playoff=False):
    """(home goals, away goals, kind) drawn from the pair's probabilities.
    Goals carry only what the standings use: a regulation winner's margin
    from the league's distribution, one goal in OT, the credited goal in
    a shootout. Playoff overtime is played to a goal."""
    p_hr, p_ho, p_ao, so = probs[(home, away)]
    u = rng.random()
    if playoff:
        tie = p_ho + p_ao
        p_h = p_hr + tie * (p_ho / tie if tie else 0.5)
        return (1, 0, "OT") if u < p_h else (0, 1, "OT")
    if u < p_hr:
        return _nhl_margin(rng), 0, "REG"
    if u < p_hr + p_ho:
        return 1, 0, ("SO" if rng.random() < so else "OT")
    if u < p_hr + p_ho + p_ao:
        return 0, 1, ("SO" if rng.random() < so else "OT")
    return 0, _nhl_margin(rng), "REG"


def play_nba(probs, home, away, rng, playoff=False):
    """(home points, away points, "REG"): the winner from the pair's
    probability, a margin only the point-differential tiebreaker reads."""
    m = max(1, int(abs(rng.gauss(0.0, 13.0))) + 1)
    return (m, 0, "REG") if rng.random() < probs[(home, away)] else (0, m, "REG")


_HOSTS = (0, 0, 1, 1, 0, 1, 0)        # 2-2-1-1-1: 0 = the better record hosts


def series(play, probs, hi, lo, rng):
    """Best of seven, `hi` (the better regular-season record) at home in
    games 1, 2, 5, 7. Returns (winner, games)."""
    wins = {hi: 0, lo: 0}
    g = 0
    while wins[hi] < 4 and wins[lo] < 4:
        h, a = (hi, lo) if _HOSTS[g] == 0 else (lo, hi)
        hs, as_, _k = play(probs, h, a, rng, playoff=True)
        wins[h if hs > as_ else a] += 1
        g += 1
    return (hi if wins[hi] == 4 else lo), g


def _better(rank, a, b):
    return (a, b) if rank[a] < rank[b] else (b, a)


def nhl_playoffs(T, field, probs, rng, rank):
    """Plays the bracket. Returns {"r2": set, "cf": set, "final": set,
    "champ": club} -- the clubs that reached each round."""
    reached = {"r2": set(), "cf": set(), "final": set(), "champ": None}
    finalists = []
    for c, f in sorted(field.items()):
        side_winners = []
        for side in f["sides"]:
            w1 = [series(play_nhl, probs, *_better(rank, a, b), rng)[0] for a, b in side["pairs"]]
            reached["r2"].update(w1)
            w2 = series(play_nhl, probs, *_better(rank, w1[0], w1[1]), rng)[0]
            side_winners.append(w2)
        reached["cf"].update(side_winners)
        cw = series(play_nhl, probs, *_better(rank, side_winners[0], side_winners[1]), rng)[0]
        finalists.append(cw)
    reached["final"].update(finalists)
    reached["champ"] = series(play_nhl, probs, *_better(rank, finalists[0], finalists[1]), rng)[0]
    return reached


def nba_play_in(probs, order, rng):
    """(seed 7, seed 8) from the conference's 7-10."""
    s7, s8, s9, s10 = order[6:10]
    hs, as_, _k = play_nba(probs, s7, s8, rng, playoff=True)
    seven, loser78 = (s7, s8) if hs > as_ else (s8, s7)
    hs, as_, _k = play_nba(probs, s9, s10, rng, playoff=True)
    w910 = s9 if hs > as_ else s10
    hs, as_, _k = play_nba(probs, loser78, w910, rng, playoff=True)
    eight = loser78 if hs > as_ else w910
    return seven, eight


def nba_playoffs(T, field, probs, rng, rank):
    reached = {"playoffs": set(), "playin": set(), "r2": set(), "cf": set(), "final": set(), "champ": None,
               "seed": {}}
    finalists = []
    for c, f in sorted(field.items()):
        order = f["order"]
        reached["playin"].update(order[6:10])
        seven, eight = nba_play_in(probs, order, rng)
        seeds = order[:6] + [seven, eight]
        for i, t in enumerate(seeds):
            reached["seed"][t] = i + 1
        reached["playoffs"].update(seeds)
        # 1v8 / 4v5 one half, 2v7 / 3v6 the other; the better record hosts
        halves = []
        for a, b in (((0, 7), (3, 4)), ((1, 6), (2, 5))):
            w = [series(play_nba, probs, *_better(rank, seeds[i], seeds[j]), rng)[0] for i, j in (a, b)]
            reached["r2"].update(w)
            halves.append(series(play_nba, probs, *_better(rank, w[0], w[1]), rng)[0])
        reached["cf"].update(halves)
        finalists.append(series(play_nba, probs, *_better(rank, halves[0], halves[1]), rng)[0])
    reached["final"].update(finalists)
    reached["champ"] = series(play_nba, probs, *_better(rank, finalists[0], finalists[1]), rng)[0]
    return reached


# ---- the season Monte Carlo -------------------------------------------------------
def _shifted(league, pr, d):
    """A pair's entry with the home side's win moved by `d` in logit (the
    two clubs' strength draws for this season). NHL: the regulation and
    past-regulation wins scale together, so the overtime share stays the
    pair's own."""
    if league == "nba":
        x = min(max(pr, 1e-6), 1 - 1e-6)
        return 1.0 / (1.0 + math.exp(-(math.log(x / (1 - x)) + d)))
    p_hr, p_ho, p_ao, so = pr
    ph = min(max(p_hr + p_ho, 1e-6), 1 - 1e-6)
    ph2 = 1.0 / (1.0 + math.exp(-(math.log(ph / (1 - ph)) + d)))
    pa, pa2 = 1 - ph, 1 - ph2
    return (p_hr * ph2 / ph, p_ho * ph2 / ph, p_ao * pa2 / pa, so)


def simulate(base, remaining, probs, n, seed=0, filler=None, noise=None):
    """Play the rest of the season n times from `base` (a Table holding the
    real results). `remaining` [(home, away)] in schedule order; `probs` the
    pair probabilities (see the module docstring); `filler` {club: games
    still unscheduled} -- the NBA Cup's December slots, paired at random
    inside each conference every season. `noise` {club: sd}: each season
    draws every club a strength shift ~ N(0, sd^2) in logit, applied to all
    of its games that season (and its playoff games) -- the uncertainty in
    a club's TRUE strength, which a season played at fixed probabilities
    leaves out and which is most of a long-range future's width. Returns
    the per-club tallies:
    `seed_hist` the regular-season conference place (Kalshi's "#N seed or
    better" settles on it, before the play-in), `fseed_hist` the NBA's
    playoff seed AFTER the play-in (its exact-seed markets settle 7 and 8 on
    the play-in), `max_w` the league's most wins (its "any team N+ wins")."""
    league = base.league
    play = play_nhl if league == "nhl" else play_nba
    rng = random.Random(seed)
    teams = list(base.conf)
    tal = {t: defaultdict(float) for t in teams}
    hist = {t: defaultdict(int) for t in teams}
    seed_hist = {t: defaultdict(int) for t in teams}
    fseed_hist = {t: defaultdict(int) for t in teams}
    max_w = defaultdict(int)
    base_probs = probs
    for _ in range(n):
        T = base.copy()
        games = list(remaining)
        if filler:
            games += _filler_games(base, filler, rng)
        if noise:
            eps = {t: rng.gauss(0.0, noise.get(t, 0.0)) for t in teams}
            probs = _ShiftedProbs(league, base_probs, eps)
        for h, a in games:
            hs, as_, kind = play(probs, h, a, rng)
            T.add(h, a, hs, as_, kind)
        rank_list = league_order(T, rng)
        rank = {t: i for i, t in enumerate(rank_list)}
        tal[rank_list[0]]["best"] += 1
        tal[rank_list[-1]]["worst"] += 1
        if league == "nhl":
            field = nhl_field(T, rng)
            for c, f in field.items():
                for d, top in f["div"].items():
                    tal[top[0]]["div"] += 1
                for i, t in enumerate(f["order"]):
                    seed_hist[t][i + 1] += 1
                for t in [x for v in f["div"].values() for x in v] + f["wc"]:
                    tal[t]["playoffs"] += 1
                for t in f["wc"]:
                    tal[t]["wildcard"] += 1
            r = nhl_playoffs(T, field, probs, rng, rank)
        else:
            field = nba_field(T, rng)
            for c, f in field.items():
                for t in f["div_winners"]:
                    tal[t]["div"] += 1
                for i, t in enumerate(f["order"]):
                    seed_hist[t][i + 1] += 1
                tal[f["order"][0]]["seed1"] += 1
            r = nba_playoffs(T, field, probs, rng, rank)
            for t, sd in r["seed"].items():
                fseed_hist[t][sd] += 1
            for t in r["playoffs"]:
                tal[t]["playoffs"] += 1
            for t in r["playin"]:
                tal[t]["playin"] += 1
        for k in ("r2", "cf", "final"):
            for t in r[k]:
                tal[t][k] += 1
        tal[r["champ"]]["champ"] += 1
        for t in teams:
            tal[t]["w"] += T.w[t]
            tal[t]["pts"] += T.pts[t]
            hist[t][T.pts[t] if league == "nhl" else T.w[t]] += 1
        max_w[max(T.w.values())] += 1
    return {"n": n, "tally": {t: dict(v) for t, v in tal.items()},
            "hist": {t: dict(v) for t, v in hist.items()},
            "seed_hist": {t: dict(v) for t, v in seed_hist.items()},
            "fseed_hist": {t: dict(v) for t, v in fseed_hist.items() if v},
            "max_w": dict(max_w)}


class _ShiftedProbs(dict):
    """The pair table as one season's strength draws see it, computed on
    first use (a season touches a few hundred of the ~1,000 pairs)."""

    def __init__(self, league, base, eps):
        super().__init__()
        self.league, self.base, self.eps = league, base, eps

    def __missing__(self, key):
        h, a = key
        v = _shifted(self.league, self.base[key], self.eps[h] - self.eps[a])
        self[key] = v
        return v


def _filler_games(base, filler, rng):
    """Pair the clubs still owed games (the NBA Cup's December slots) at
    random inside each conference, alternating home and away."""
    out = []
    by_conf = defaultdict(list)
    for t, k in filler.items():
        by_conf[base.conf[t]].extend([t] * int(k))
    for c, pool in by_conf.items():
        rng.shuffle(pool)
        i = 0
        while i + 1 < len(pool):
            a, b = pool[i], pool[i + 1]
            if a == b:
                j = next((k for k in range(i + 2, len(pool)) if pool[k] != a), None)
                if j is None:
                    break
                pool[i + 1], pool[j] = pool[j], pool[i + 1]
                b = pool[i + 1]
            out.append((a, b) if len(out) % 2 == 0 else (b, a))
            i += 2
    return out


# ---- the season's games from ESPN ---------------------------------------------------
_PATH = {"nba": "basketball/nba", "nhl": "hockey/nhl"}
_MONTHS = (9, 10, 11, 12, 1, 2, 3, 4, 5, 6)
_NHL_KIND = {3: "REG", 4: "OT", 5: "SO"}


def espn_games(league, season_end, get=None):
    """Every regular-season game of the season ending in `season_end`, one
    ESPN scoreboard call a month (a date RANGE answers 400 and a whole year
    stops at 1,000 events, short of either league's season):
    [{"id", "date", "home", "away", "hs", "as", "kind", "final"}], schedule
    order. NHL `kind` from the period the game ended in (3 regulation, 4
    overtime, 5 shootout -- ESPN's shootout score already carries the
    winner's credited goal). The NBA Cup championship is left out: ESPN
    files it as a regular-season game and it is the one Cup game that does
    not count in the standings (competition type "CC"; 2023-24 LAL-IND,
    2024-25 OKC-MIL, 2025-26 NY-SA). All-Star games ride in as
    regular-season games too; Table.add drops any club outside the
    alignment."""
    if get is None:
        import racing
        get = racing._get_json
    seen = {}
    for m in _MONTHS:
        y = season_end if m <= 6 else season_end - 1
        d = get(f"https://site.api.espn.com/apis/site/v2/sports/{_PATH[league]}/scoreboard"
                f"?dates={y}{m:02d}&limit=1000", timeout=30) or {}
        for e in d.get("events") or []:
            if (e.get("season") or {}).get("type") != 2:
                continue
            c = (e.get("competitions") or [{}])[0]
            if ((c.get("type") or {}).get("abbreviation") or "") == "CC":
                continue
            side = {x.get("homeAway"): x for x in c.get("competitors") or []}
            if "home" not in side or "away" not in side:
                continue
            st = c.get("status") or {}
            final = (st.get("type") or {}).get("completed") is True
            try:
                hs, as_ = int(side["home"].get("score") or 0), int(side["away"].get("score") or 0)
            except (TypeError, ValueError):
                hs = as_ = 0
            kind = "REG"
            if league == "nhl":
                kind = _NHL_KIND.get(int(st.get("period") or 3), "REG")
            seen[e["id"]] = {"id": e["id"], "date": e.get("date") or "",
                             "home": side["home"]["team"]["abbreviation"],
                             "away": side["away"]["team"]["abbreviation"],
                             "hs": hs, "as": as_, "kind": kind, "final": final and hs != as_}
    return sorted(seen.values(), key=lambda g: (g["date"], g["id"]))


def pct(x, n):
    return round(100.0 * x / n, 1) if n else 0.0


def logistic(x):
    return 1.0 / (1.0 + math.exp(-x))
