"""Soccer: every Kalshi soccer match, priced by a goals model.

The owner's soccer bet is NO on the underdog -- it pays on the favourite's
win AND on the draw -- so the board is built around that leg: for every
match Kalshi lists (3-way events: home win / away win / tie, settled after
90 minutes plus stoppage, no extra time or penalties), the model's chance
that the underdog does NOT win beside Kalshi's NO price on the underdog.

  MARKETS   Every Kalshi series tagged Soccer whose ticker ends in GAME
            (discovered from /series, cached 6h; ~80 leagues, ~900 open
            events in mid-October). The EVENT ticker's two team codes are
            HOME then AWAY (638 of 640 ESPN-matched events, 2026-10-16;
            the two exceptions were neutral-site cup ties), which is how a
            match in a league ESPN does not carry still knows its home side.
            Kalshi's market TITLES ("X vs Y") are in no fixed order (336 home
            first, 311 away first on the same sample) and are never used for
            that.
  DATA      ESPN: one call per league per calendar year returns every result
            (scoreboard?dates=YYYY&limit=1000 -- 85,067 matches over 94
            competitions, 2023-2026), and the all-leagues scoreboard per date
            lists every upcoming match with the team ids this module keys
            on, and DraftKings' three-way line where ESPN carries it.
            Leagues Kalshi lists that ESPN does not (Serie C, 3. Liga,
            Korea, Poland, Czech Republic, Egypt, Thailand, ...) show the
            de-vigged Kalshi price and say there is no model.
  MODEL     A Dixon-Coles goals model fitted in pure Python (the server has
            no numpy): every team an attack and a defence rate, a home
            advantage per competition, results weighted by recency, every
            rating shrunk toward its league's mean with the strength of
            KAPPA matches, the leagues tied to one scale by their
            cross-league matches (Champions League, Libertadores, AFC, the
            domestic cups). Scorelines are the exact Poisson grid with the
            Dixon-Coles low-score correction RHO, not a Monte Carlo: the
            answer is the same every build. Parameters chosen on 2024 by a
            rule written first and tested on 2025 (research/soccer_validation.py,
            research/reports/soccer_validation.md). A match that went to
            extra time enters at its 90-minute score (_ninety).
  FORM/H2H  Tested on 2024/2025/2026 by a rule written first
            (research/reports/soccer_form.md): neither recent form nor the
            head-to-head record improved held-out predictions, so both carry
            weight 0 (W_FORM, W_H2H) and the card SHOWS them -- each side's
            last five, the past meetings -- as information.
  WHERE     The fit and its ~200 ESPN year files run in a niced child every
            six hours (refit_main, under deep_cache.HEAVY_BUILD) and land in
            the deep store; the board's build thread only loads the result.
  RECORD    Every priced outcome logs its raw model probability to predlog
            bucket "soccer" against Kalshi's de-vigged price; DraftKings'
            de-vigged line, where ESPN shows one, logs to the shadow bucket
            "soccer_dk" so the live record says which of the three knew more.
"""
import datetime
import math
import re
import time
import unicodedata

import clock
import errlog
import kalshi
import racing

# ---- the fitted parameters -------------------------------------------------
# Chosen on 2024 (every month predicted from the fit of everything before it)
# and frozen before 2025 was read: research/reports/soccer_validation.md.
XI = 0.003            # recency: a result's weight halves every ln2/XI = 231 days
KAPPA = 3.0           # shrinkage: KAPPA matches of league-average play behind every rating
RHO = 0.0             # Dixon-Coles dependence at 0-0, 1-0, 0-1, 1-1: the grid chose none
HFA_KAPPA = 60.0      # a competition's home advantage leans on the global one with this many matches
SWEEPS = 40           # fit sweeps: on two real seasons the largest rating moves 0.07 between 24 and 48, under 0.01 past 40
MAX_GOALS = 10        # the scoreline grid; P(11+) is below 1e-5 at any fitted rate
HEAVY_GAP = 5.0       # the owner's filter: underdog YES ask at least this far under the tie's
# A Kalshi book is only READ when its three YES asks sum to this or less
# (an overround of 15c). On 2026-10-16, 260 of 1,014 quoted soccer books were
# placeholders -- typically 95c/95c/95c asks over 5c bids, no volume -- and the
# first version read them: the "underdog" was whichever ask was a cent
# lower (41 false heavy-underdog flags), edges were taken against a 95c NO
# that no one was selling, and a 33/33/33 "fair" price went into the record as
# the benchmark. The sums break cleanly: 739 books at 115c or less, 15
# between 115c and 150c, the 260 above.
MAX_BOOK = 115.0

# ---- recent form and head-to-head ---------------------------------------------
# research/soccer_form_validation.py, research/reports/soccer_form.md: the
# rule was written before the run; each term ships at the weight 2024 chose
# only if 2025 confirmed it on its own by two standard errors (and 2026 did
# not contradict it). A weight of 0 still SHOWS the term on the card.
# The 2026-10-17 run (58,183 predictions, weekly refits in production's
# window): 2024 chose the short-memory blend (half-life 35 days) at 0.1 and NO
# head-to-head; on 2025 the blend gained 0.00005 log loss per match against a
# standard error of 0.00015, so it ships at 0. Every head-to-head weight made
# 2024 worse (+0.0028 at 0.25, +0.0393 at 1.0), and the last-N-matches form
# made it worse at every weight (+0.0003 to +0.016). The rating's own
# 231-day half-life already carries what recent results say.
FORM_KIND = "A"       # "A": a short-memory fit blended in; "B": the last N matches' residual
FORM_PARAM = 0.02     # A: the short fit's xi; B: N
W_FORM = 0.0          # 2024 chose 0.1; 2025 did not confirm it (research/reports/soccer_form.md)
W_H2H = 0.0           # 2024 chose 0
FORM_PRIOR = 2.0      # expected goals of prior in every residual: two lucky games are not form
# Shots as a rating input (research/soccer_shots_validation.py, rule written
# first): the fit reads each side's PSEUDO-GOALS, (1 - SHOTS_W) goals +
# SHOTS_W x, x = SHOTS_COEF[0] on-target + SHOTS_COEF[1] other shots (least
# squares on 2023, scaled to 2023's goals; once shots on target are counted,
# more off-target shots mean slightly FEWER goals -- teams chasing a game
# shoot from distance). 0.25 chosen on 2024, beat goals alone on 2025
# (-0.00169 +/- 0.00033 three-way log loss a match) and on 2026 (-0.00156 +/-
# 0.00037), the BTTS figure unharmed. A match without shots (a quarter of
# ESPN's; extra time, whose shots cover 120 minutes) enters at its goals.
# Every prediction, the form panel and the BTTS base rate stay on real goals.
SHOTS_W = 0.25
SHOTS_COEF = (0.3773, -0.02771)
# The model against the closing market (research/soccer_market_validation.py,
# rule written first; Pinnacle's closing three-way price from
# football-data.co.uk, 24,841 matches in 27 leagues joined to ESPN's). The
# model lost to the market alone in EVERY league and every year -- 2025 by
# 0.0192 +/- 0.0018 log loss a match, 2026 by 0.0219 +/- 0.0020 -- and every
# weight on the model made the market's price worse (2024 chose 0 of a grid
# down to 0.05). So the card's headline chance is the logarithmic pool of
# the model and Kalshi's de-vigged price at MODEL_WEIGHT on the model: 0,
# Kalshi's own price. Kalshi's pre-game book is thinner and earlier than
# Pinnacle at the close, so its true weight may sit above 0; nothing
# measured says so yet. The model's own figure stays on the card, on the
# record (bucket "soccer", graded against Kalshi) and in the quick picks'
# disagreement rule.
MODEL_WEIGHT = 0.0


def headline(model3, fair3, w=None):
    """{"home", "draw", "away"}: the logarithmic pool of the model's three-way
    probabilities and Kalshi's de-vigged ones, weight `w` (MODEL_WEIGHT) on
    the model, renormalised."""
    w = MODEL_WEIGHT if w is None else w
    z = {k: math.exp(w * math.log(max(1e-12, model3[k])) + (1 - w) * math.log(max(1e-12, fair3[k])))
         for k in ("home", "draw", "away")}
    t = sum(z.values())
    return {k: v / t for k, v in z.items()}
_LAST = 8             # matches kept per team in the artifact (the card shows five)

# Years of results behind the fit. With XI = 0.003 a result two years old
# weighs e^-2.2 = 11% of today's; the validation fitted on the same window.
_FIT_YEARS = 2

# ---- Kalshi series -> ESPN league --------------------------------------------
# Verified 2026-10-16 by matching every open event's two teams against
# ESPN's schedule for the ticker's date +-1 (research: 640 of 660 matched,
# the rest name spellings fixed in _ALIAS). None = ESPN has no such league:
# listed with the price only.
ESPN_LEAGUE = {
    "KXEPLGAME": "eng.1", "KXEFLCHAMPIONSHIPGAME": "eng.2", "KXEFLL1GAME": "eng.3",
    "KXFACUPGAME": "eng.fa", "KXEFLCUPGAME": "eng.league_cup", "KXEWSLGAME": "eng.w.1",
    "KXLALIGAGAME": "esp.1", "KXLALIGA2GAME": "esp.2", "KXCOPADELREYGAME": "esp.copa_del_rey",
    "KXLIGAFGAME": "esp.w.1", "KXESPSUPERCUPGAME": "esp.super_cup",
    "KXSERIEAGAME": "ita.1", "KXSERIEBGAME": "ita.2", "KXCOPPAITALIAGAME": "ita.coppa_italia",
    "KXBUNDESLIGAGAME": "ger.1", "KXBUNDESLIGA2GAME": "ger.2", "KXDFBPOKALGAME": "ger.dfb_pokal",
    "KXLIGUE1GAME": "fra.1", "KXLIGUE2GAME": "fra.2", "KXCOUPEDEFRANCEGAME": "fra.coupe_de_france",
    "KXFRAPLWGAME": "fra.w.1",
    "KXUCLGAME": "uefa.champions", "KXUELGAME": "uefa.europa", "KXUECLGAME": "uefa.europa.conf",
    "KXUCLWGAME": "uefa.wchampions", "KXUEFANLGAME": "uefa.nations", "KXUEFASCGAME": "uefa.super_cup",
    "KXMLSGAME": "usa.1", "KXUSLGAME": "usa.usl.1", "KXNWSLGAME": "usa.nwsl", "KXUSOPENCUPGAME": "usa.open",
    "KXLEAGUESCUPGAME": "concacaf.leagues.cup", "KXCONCACAFCCUPGAME": "concacaf.champions",
    "KXNCAAMSOCCERGAME": "usa.ncaa.m.1",
    "KXLIGAMXGAME": "mex.1", "KXLIGAEXPGAME": "mex.2",
    "KXBRASILEIROGAME": "bra.1", "KXBRASILEIROBGAME": "bra.2", "KXCOPADOBRASILGAME": "bra.copa_do_brazil",
    "KXARGPREMDIVGAME": "arg.1", "KXARGNACBGAME": "arg.2",
    "KXDIMAYORGAME": "col.1", "KXURYPDGAME": "uru.1", "KXPERLIGA1GAME": "per.1", "KXECULPGAME": "ecu.1",
    "KXBOLPDIVGAME": "bol.1", "KXVENFUTVEGAME": "ven.1", "KXAPFDDHGAME": "par.1", "KXCHLLDPGAME": "chi.1",
    "KXCONMEBOLLIBGAME": "conmebol.libertadores", "KXCONMEBOLSUDGAME": "conmebol.sudamericana",
    "KXJLEAGUEGAME": "jpn.1", "KXSAUDIPLGAME": "ksa.1", "KXAFCCLGAME": "afc.champions",
    "KXSUPERLIGGAME": "tur.1", "KXLIGAPORTUGALGAME": "por.1", "KXTACAPORTGAME": "por.taca.portugal",
    "KXEREDIVISIEGAME": "ned.1", "KXEERSTEDIVGAME": "ned.2", "KXKNVBCUPGAME": "ned.cup",
    "KXEREDIVISIEWGAME": "ned.w.1", "KXBELGIANPLGAME": "bel.1",
    "KXSCOTTISHPREMGAME": "sco.1", "KXSCOCUPGAME": "sco.tennents",
    "KXELITESERIENGAME": "nor.1", "KXALLSVENSKANGAME": "swe.1",
    "KXDENSUPERLIGAGAME": "den.1", "KXDANISHSUPERLIGAGAME": "den.1",
    "KXAUTBUNDESGAME": "aut.1", "KXSLGREECEGAME": "gre.1",
    "KXINDSLGAME": "ind.1", "KXALEAGUEGAME": "aus.1", "KXSAPSLGAME": "rsa.1", "KXCHNSLGAME": "chn.1",
    "KXINTLFRIENDLYGAME": "fifa.friendly", "KXCLUBFGAME": "club.friendly",
    "KXWCGAME": "fifa.world", "KXAFCONGAME": "caf.nations",
}
# Competitions whose results tie the domestic leagues to one scale, fetched
# for the fit whether or not Kalshi lists them this week.
_LINK_LEAGUES = ("uefa.champions", "uefa.europa", "uefa.europa.conf", "uefa.champions_qual",
                 "uefa.europa_qual", "uefa.europa.conf_qual", "conmebol.libertadores",
                 "conmebol.sudamericana", "afc.champions", "concacaf.champions",
                 "concacaf.leagues.cup", "caf.champions", "caf.confed",
                 "eng.fa", "eng.league_cup", "esp.copa_del_rey", "ita.coppa_italia",
                 "ger.dfb_pokal", "fra.coupe_de_france", "ned.cup", "por.taca.portugal",
                 "bra.copa_do_brazil", "usa.open", "sco.tennents",
                 "eng.4", "arg.3", "usa.usl.l1")
# Competitions where the model does worse than the league base rate, pooled
# over every held-out week of 2024, 2025 and 2026 (n >= 100,
# research/reports/soccer_form.md): only the Scottish Cup (+0.018 log loss
# a match, 256 matches). The first version flagged seven cups off 2025 alone
# -- an artifact of dropping the matches that went to extra time, which took
# the cups' draws out of the benchmark as well as the fit. The card warns;
# the price is still shown.
_WEAK_COMPS = {"sco.tennents"}
# Competitions whose home side is not at home: no home advantage is fitted or applied.
# (NCAA men's soccer was on this list in the first version; college sides
# play at home like anyone else, and ESPN's neutralSite flags the few that
# do not.)
_NEUTRAL = {"fifa.friendly", "club.friendly", "fifa.world", "caf.nations", "uefa.super_cup",
            "esp.super_cup", "ita.super_cup"}

_SITE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
_ESPN_FINAL = {"STATUS_FULL_TIME", "STATUS_FINAL"}       # the score IS the 90-minute score
_ESPN_EXTRA = {"STATUS_FINAL_AET", "STATUS_FINAL_PEN"}   # the score includes extra time
_MINUTE_RE = re.compile(r"^(\d+)'")


# ---- ESPN results ----------------------------------------------------------
def _espn(url, timeout=40):
    return racing._get_json(url, timeout=timeout)


def _ninety(c, h, a, hg, ag):
    """The 90-minute score of a match that went to extra time, rebuilt from
    the goal events, or None when the events do not account for the final
    score. A match that went to extra time was NOT always level at 90: a
    second leg goes on when the AGGREGATE is level (PSV 3-1 Juventus AET,
    2025, was 2-1 at 90), so the first version's choice -- drop every such
    match -- took ~375 cup results a year out of the fit and the test, and
    the other obvious choice -- call them all draws -- would have been wrong
    on every second leg. ESPN writes stoppage as "90'+3'" and extra time as
    "98'": regulation is a base minute of 90 or less. An own goal's `team`
    is checked both ways against the final score."""
    hid, aid = str(h["team"]["id"]), str(a["team"]["id"])
    goals = []
    for g in c.get("details") or []:
        if not g.get("scoringPlay") or g.get("shootout"):
            continue
        m = _MINUTE_RE.match(str((g.get("clock") or {}).get("displayValue") or ""))
        tid = str((g.get("team") or {}).get("id"))
        if not m or tid not in (hid, aid):
            return None
        goals.append((int(m.group(1)), tid, bool(g.get("ownGoal"))))
    for own_to_other in (False, True):
        tot = {hid: 0, aid: 0}
        reg = {hid: 0, aid: 0}
        for minute, tid, og in goals:
            to = (aid if tid == hid else hid) if (og and own_to_other) else tid
            tot[to] += 1
            if minute <= 90:
                reg[to] += 1
        if tot[hid] == hg and tot[aid] == ag:
            return reg[hid], reg[aid]
    return None


def _stat(x, name):
    for st in x.get("statistics") or []:
        if st.get("name") == name:
            try:
                return float(st.get("displayValue"))
            except (TypeError, ValueError):
                return None
    return None


def parse_scoreboard(d, slug, shots=None):
    """(rows, names) from one ESPN scoreboard payload: rows are the finished
    matches as (date_ordinal, slug, home_id, away_id, home_goals_90,
    away_goals_90, neutral), names {team_id: short name}. Kalshi settles on
    90 minutes plus stoppage, so an extra-time result enters at its
    90-minute score (_ninety) or not at all. `shots`, a dict, is filled with
    {row[:4]: (home on target, away on target, home shots, away shots)} for
    every row whose payload carries them (SHOTS_W) -- not for an extra-time
    match, whose shots cover 120 minutes against a 90-minute score."""
    rows, names = [], {}
    for e in d.get("events") or []:
        c = (e.get("competitions") or [{}])[0]
        st = ((c.get("status") or {}).get("type") or {})
        cs = c.get("competitors") or []
        h = next((x for x in cs if x.get("homeAway") == "home"), None)
        a = next((x for x in cs if x.get("homeAway") == "away"), None)
        if not h or not a:
            continue
        for x in (h, a):
            t = x.get("team") or {}
            if t.get("id"):
                names[str(t["id"])] = t.get("shortDisplayName") or t.get("displayName") or str(t["id"])
        if st.get("state") != "post" or st.get("name") not in (_ESPN_FINAL | _ESPN_EXTRA):
            continue
        try:
            hg, ag = int(h.get("score")), int(a.get("score"))
            day = datetime.date.fromisoformat((e.get("date") or "")[:10]).toordinal()
        except (TypeError, ValueError):
            continue
        extra = st.get("name") in _ESPN_EXTRA
        if extra:
            r = _ninety(c, h, a, hg, ag)
            if r is None:
                continue
            hg, ag = r
        row = (day, slug, str(h["team"]["id"]), str(a["team"]["id"]), hg, ag,
               bool(c.get("neutralSite")) or slug in _NEUTRAL)
        rows.append(row)
        if shots is not None and not extra:
            sh = (_stat(h, "shotsOnTarget"), _stat(a, "shotsOnTarget"), _stat(h, "totalShots"), _stat(a, "totalShots"))
            if all(v is not None for v in sh) and sh[2] >= sh[0] and sh[3] >= sh[1]:
                shots[row[:4]] = sh
    return rows, names


def pseudo_rows(rows, shots, w=None, coef=None):
    """The rows the FIT reads: each side's goals replaced by (1 - w) goals +
    w x, x = coef[0] on target + coef[1] other shots, where the match has
    shots (SHOTS_W, SHOTS_COEF); a row without them is unchanged."""
    w = SHOTS_W if w is None else w
    c_on, c_off = SHOTS_COEF if coef is None else coef
    if not w or not shots:
        return list(rows)
    out = []
    for r in rows:
        sh = shots.get(r[:4])
        if sh is None:
            out.append(r)
            continue
        xh = c_on * sh[0] + c_off * (sh[2] - sh[0])
        xa = c_on * sh[1] + c_off * (sh[3] - sh[1])
        out.append(r[:4] + ((1 - w) * r[4] + w * xh, (1 - w) * r[5] + w * xa) + r[6:])
    return out


def results(slug, year):
    """(rows, names) for one ESPN league in one calendar year (parse_scoreboard).
    Cached 6h for the current year, a week for past years."""
    cur = clock.today_et().year

    def build():
        return parse_scoreboard(_espn(f"{_SITE}/{slug}/scoreboard?dates={year}&limit=1000"), slug)
    ttl = 6 * 3600 if year >= cur else 7 * 24 * 3600
    return racing._cached(("soccer_results", slug, year), ttl, build) or ([], {})


def history(slugs, asof=None, workers=2, shots=None):
    """(rows, names): every finished match behind the fit -- the given
    leagues plus the link competitions, the last _FIT_YEARS calendar years --
    and {team_id: short name}; `shots`, a dict, is filled as
    parse_scoreboard fills it. A past year is read from the deep store
    (refetched when missing or a week old: late corrections; a league stored
    before shots were kept -- a (rows, names) pair -- is refetched once);
    the current year is fetched every refit. Two fetches at a time: a year
    file is 4-6 MB of JSON and ~20 MB once parsed, and this runs in a child
    beside the web workers. A league that fails is skipped (and ledgered),
    never the build. Runs in the refit child only (refit_main)."""
    import concurrent.futures as cf
    import deep_cache
    asof = asof or clock.today_et()
    want = sorted(set(s for s in slugs if s) | set(_LINK_LEAGUES))
    out, names = [], {}

    def fetch(job):
        try:
            sh = {}
            rows, nm = parse_scoreboard(_espn(f"{_SITE}/{job[0]}/scoreboard?dates={job[1]}&limit=1000"), job[0], shots=sh)
            return job, (rows, nm, sh)
        except Exception as e:
            errlog.note("SOC-results", e, path=f"{job[0]}/{job[1]}")
            return job, None
    for k in range(_FIT_YEARS):
        year = asof.year - k
        store, have = {}, {}
        if year < asof.year:
            have, _ts = deep_cache.load(f"soccer_rows_{year}")
            have = {k: v for k, v in (have or {}).items() if len(v) == 3}
            if (deep_cache.age(f"soccer_rows_{year}") or 1e9) > 7 * 24 * 3600:
                have = {}
        jobs = [(slug, year) for slug in want if slug not in have]
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            for (slug, _y), got in ex.map(fetch, jobs):
                if got is not None:
                    have[slug] = got
        for slug in want:
            if slug in have:
                rows, nm, sh = have[slug]
                out.extend(tuple(r) for r in rows)
                names.update(nm)
                if shots is not None:
                    shots.update(sh)
                store[slug] = have[slug]
        if year < asof.year and jobs:
            try:
                deep_cache.save(f"soccer_rows_{year}", store)
            except Exception as e:
                errlog.note("SOC-rows-save", e)
    cutoff = asof.toordinal()
    return sorted(set(m for m in out if m[0] < cutoff)), names


# ---- the Dixon-Coles fit (pure Python) -----------------------------------------
def _empty():
    return {"mu": 0.0, "att": {}, "dfn": {}, "hfa": {}, "hfa_global": 0.0, "league": {}, "n": 0,
            "lmean": {"*": (0.0, 0.0)}}


def fit(matches, asof_ord, xi=XI, kappa=KAPPA, hfa_kappa=HFA_KAPPA, sweeps=SWEEPS):
    """Attack and defence rates per team, home advantage per competition.

    lambda_home = exp(mu + hfa[comp] + att[h] - dfn[a])
    lambda_away = exp(mu + att[a] - dfn[h])

    Each parameter takes its Poisson maximum-likelihood value given the
    others (a closed form: log of weighted goals over weighted exposure),
    with a Gamma prior worth `kappa` matches at the LEAGUE'S mean rate --
    a promoted side or a club with three results is pulled to its league's
    average, a club with a season of data barely moves. A league's mean is
    itself shrunk toward the global mean by two pseudo-teams, which is what
    pins a league no cross-league match reaches (without it, a league's
    attack and defence could drift together by any constant). Results are
    weighted exp(-xi * days before asof). Returns {"mu", "att", "dfn",
    "hfa", "hfa_global", "league", "n"}; empty when nothing to fit."""
    if not matches:
        return _empty()
    rows = []
    tot_g = tot_w = 0.0
    for day, comp, h, a, hg, ag, neutral in matches:
        w = math.exp(-xi * max(0, asof_ord - day))
        if w < 1e-4:
            continue
        rows.append((comp, h, a, hg, ag, w, 0.0 if neutral else 1.0))
        tot_g += w * (hg + ag)
        tot_w += 2.0 * w
    if not rows:
        return _empty()
    mu = math.log(max(0.2, tot_g / tot_w))
    # a team's league: the competition it plays most (by weight); and the
    # weighted match counts the priors are scaled by
    seen, n_t, n_c = {}, {}, {}
    for comp, h, a, hg, ag, w, hm in rows:
        for t in (h, a):
            d = seen.setdefault(t, {})
            d[comp] = d.get(comp, 0.0) + w
            n_t[t] = n_t.get(t, 0.0) + w
        if hm:
            n_c[comp] = n_c.get(comp, 0.0) + w
    league = {t: max(d.items(), key=lambda kv: kv[1])[0] for t, d in seen.items()}
    teams = list(seen)
    att = {t: 0.0 for t in teams}
    dfn = {t: 0.0 for t in teams}
    comps = sorted({r[0] for r in rows})
    hfa = {c: 0.0 for c in comps}
    hs = 0.0
    nonneutral = [c for c in comps if c not in _NEUTRAL and n_c.get(c)]
    for _ in range(sweeps):
        # league means, shrunk toward the global mean by two pseudo-teams
        ga = sum(att.values()) / len(teams)
        gd = sum(dfn.values()) / len(teams)
        la_s, ld_s, ln = {}, {}, {}
        for t in teams:
            lg = league[t]
            la_s[lg] = la_s.get(lg, 0.0) + att[t]
            ld_s[lg] = ld_s.get(lg, 0.0) + dfn[t]
            ln[lg] = ln.get(lg, 0) + 1
        la = {lg: (la_s[lg] + 2.0 * ga) / (ln[lg] + 2) for lg in ln}
        ld = {lg: (ld_s[lg] + 2.0 * gd) / (ln[lg] + 2) for lg in ln}
        # The three blocks are updated in turn, each from the others' LATEST
        # values (Gauss-Seidel). Updating all three from one snapshot
        # oscillated: on a synthetic three-league season it recovered the
        # ratings at a correlation of 0.1 and put the home advantage at 3.3.
        # attack, given defence and home advantage
        G = dict.fromkeys(teams, 0.0); E = dict.fromkeys(teams, 0.0)
        for comp, h, a, hg, ag, w, hm in rows:
            G[h] += w * hg; E[h] += w * math.exp(mu + hfa[comp] * hm - dfn[a])
            G[a] += w * ag; E[a] += w * math.exp(mu - dfn[h])
        for t in teams:
            # the prior's kappa pseudo-matches sit at the team's own exposure
            # per match, so kappa means "kappa matches" whatever the opposition
            ex = E[t] / n_t[t]
            att[t] = math.log((G[t] + kappa * math.exp(la[league[t]]) * ex) / (E[t] + kappa * ex))
        # defence, given the new attack
        C = dict.fromkeys(teams, 0.0); F = dict.fromkeys(teams, 0.0)
        for comp, h, a, hg, ag, w, hm in rows:
            C[a] += w * hg; F[a] += w * math.exp(mu + hfa[comp] * hm + att[h])
            C[h] += w * ag; F[h] += w * math.exp(mu + att[a])
        for t in teams:
            fx = F[t] / n_t[t]
            dfn[t] = -math.log((C[t] + kappa * math.exp(-ld[league[t]]) * fx) / (F[t] + kappa * fx))
        # one exact degeneracy -- every attack and every defence up by the
        # same constant changes no rate -- pinned by centring the attacks
        c = sum(att.values()) / len(teams)
        for t in teams:
            att[t] -= c
            dfn[t] -= c
        # home advantage per competition, given both
        HG = dict.fromkeys(comps, 0.0); HE = dict.fromkeys(comps, 0.0)
        for comp, h, a, hg, ag, w, hm in rows:
            if hm:
                HG[comp] += w * hg
                HE[comp] += w * math.exp(mu + att[h] - dfn[a])
        hs = sum(hfa[c] for c in nonneutral) / max(1, len(nonneutral))
        for c in comps:
            if c not in nonneutral:
                hfa[c] = 0.0
                continue
            ex = HE[c] / n_c[c]
            hfa[c] = math.log((HG[c] + hfa_kappa * math.exp(hs) * ex) / (HE[c] + hfa_kappa * ex))
    model = {"mu": mu, "att": att, "dfn": dfn, "hfa": hfa, "hfa_global": hs,
             "league": league, "n": len(rows)}
    model["lmean"] = league_means(model)
    return model


def league_means(model):
    """{competition: (mean attack, mean defence)} plus "*" for everyone --
    what an unknown team is rated at. Computed once per fit: rates() used to
    rebuild the league index on every call, 2,454 teams for each of 650
    matches a build."""
    att, dfn = model["att"], model["dfn"]
    acc = {}
    for t, lg in model["league"].items():
        s = acc.setdefault(lg, [0.0, 0.0, 0])
        s[0] += att[t]; s[1] += dfn[t]; s[2] += 1
    out = {lg: (v[0] / v[2], v[1] / v[2]) for lg, v in acc.items()}
    out["*"] = ((sum(att.values()) / len(att), sum(dfn.values()) / len(dfn)) if att else (0.0, 0.0))
    return out


def rates(model, home, away, comp, neutral=False):
    """(lambda_home, lambda_away) for a match; an unknown team plays at its
    competition's -- failing that the global -- mean."""
    att, dfn = model["att"], model["dfn"]
    lm = model.get("lmean")
    if lm is None:
        lm = model["lmean"] = league_means(model)
    ma, md = lm.get(comp) or lm["*"]
    a_h = att.get(home, ma); d_h = dfn.get(home, md)
    a_a = att.get(away, ma); d_a = dfn.get(away, md)
    h = 0.0 if (neutral or comp in _NEUTRAL) else model["hfa"].get(comp, model.get("hfa_global", 0.0))
    return math.exp(model["mu"] + h + a_h - d_a), math.exp(model["mu"] + a_a - d_h)


def _tau(x, y, lh, la, rho):
    if x == 0 and y == 0:
        return 1.0 - lh * la * rho
    if x == 0 and y == 1:
        return 1.0 + lh * rho
    if x == 1 and y == 0:
        return 1.0 + la * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def grid(lh, la, rho=RHO, max_goals=MAX_GOALS):
    """The scoreline probabilities {(h, a): p}, Dixon-Coles corrected and
    renormalised over the grid."""
    ph = [math.exp(-lh) * lh ** k / math.factorial(k) for k in range(max_goals + 1)]
    pa = [math.exp(-la) * la ** k / math.factorial(k) for k in range(max_goals + 1)]
    g = {}
    tot = 0.0
    for x in range(max_goals + 1):
        for y in range(max_goals + 1):
            p = ph[x] * pa[y] * _tau(x, y, lh, la, rho)
            if p < 0:
                p = 0.0
            g[(x, y)] = p
            tot += p
    return {k: v / tot for k, v in g.items()}


def outcome(g):
    """(p_home, p_draw, p_away) from a scoreline grid."""
    h = sum(p for (x, y), p in g.items() if x > y)
    d = sum(p for (x, y), p in g.items() if x == y)
    return h, d, 1.0 - h - d


def predict(model, home, away, comp, neutral=False, rho=RHO):
    """Everything the card shows for one match."""
    lh, la = rates(model, home, away, comp, neutral)
    g = grid(lh, la, rho)
    ph, pd, pa = outcome(g)
    top = sorted(g.items(), key=lambda kv: -kv[1])[:5]
    return {"p_home": round(ph, 4), "p_draw": round(pd, 4), "p_away": round(pa, 4),
            "exp_home": round(lh, 2), "exp_away": round(la, 2),
            "over25": round(sum(p for (x, y), p in g.items() if x + y > 2.5), 3),
            "btts": round(sum(p for (x, y), p in g.items() if x > 0 and y > 0), 3),
            "scorelines": [{"score": f"{x}-{y}", "pct": round(100 * p, 1)} for (x, y), p in top],
            "known": {"home": home in model["att"], "away": away in model["att"]}}


# ---- Kalshi -------------------------------------------------------------------
_MON = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
_EVT_RE = re.compile(r"^([A-Z0-9]+)-(\d{2})([A-Z]{3})(\d{2})([A-Z0-9]+)$")


def soccer_series():
    """Every Kalshi series tagged Soccer whose ticker ends in GAME, with its
    title: {ticker: title}. Cached 6h -- new leagues appear on their own."""
    def build():
        d = kalshi._get_json(f"{kalshi.BASE}/series?category=Sports&limit=1000", timeout=30)
        out = {}
        for s in d.get("series") or []:
            t = s.get("ticker") or ""
            if "Soccer" in (s.get("tags") or []) and t.endswith("GAME"):
                out[t] = s.get("title") or t
        return out or None
    return racing._cached(("soccer_series",), 6 * 3600, build) or {}


def _fetch_series(series):
    out, cursor = [], None
    for _ in range(6):
        url = f"{kalshi.BASE}/markets?series_ticker={series}&status=open&limit=200"
        if cursor:
            url += f"&cursor={cursor}"
        d = kalshi._get_json(url, timeout=20)
        ms = d.get("markets") or []
        out.extend(ms)
        cursor = d.get("cursor")
        if not cursor or not ms:
            break
    return out


# A series with no open markets is looked at again after an hour, not every
# build: 70 of the 149 soccer GAME series were empty on 2026-10-16, and each
# board build paid a Kalshi call for every one of them.
_EMPTY_RECHECK = 3600
_empty_series = {}


def kalshi_events():
    """Every open soccer game event: {event_ticker: {series, date (the ET
    date in the ticker), home_code, away_code, markets: {code|'TIE':
    {ticker, title, name, yes_bid, yes_ask, no_bid, no_ask, volume, close}}}}.
    Fetched one series at a time with a short pause (eighty series; a burst
    reads 429 from Kalshi). Cached 4 minutes."""
    def build():
        series = soccer_series()
        out = {}
        now = time.time()
        for i, (ser, title) in enumerate(sorted(series.items())):
            if now - _empty_series.get(ser, -1e18) < _EMPTY_RECHECK:
                continue
            try:
                ms = _fetch_series(ser)
            except Exception as e:
                errlog.note("SOC-kalshi", e, path=ser)
                continue
            if not ms:
                _empty_series[ser] = now
                continue
            _empty_series.pop(ser, None)
            for m in ms:
                et = m.get("event_ticker") or ""
                r = _EVT_RE.match(et)
                if not r:
                    continue
                code = (m.get("ticker") or "").rsplit("-", 1)[-1]
                ev = out.setdefault(et, {"event": et, "series": ser, "league": title,
                                         "date": None, "home_code": None, "away_code": None,
                                         "markets": {}})
                try:
                    ev["date"] = datetime.date(2000 + int(r.group(2)), _MON[r.group(3)],
                                               int(r.group(4))).isoformat()
                except (KeyError, ValueError):
                    pass
                ev["markets"][code] = {
                    "ticker": m.get("ticker"), "title": m.get("title"),
                    "name": (m.get("yes_sub_title") or "").strip(),
                    "yes_bid": kalshi._cents(m.get("yes_bid_dollars")),
                    "yes_ask": kalshi._cents(m.get("yes_ask_dollars")),
                    "no_bid": kalshi._cents(m.get("no_bid_dollars")),
                    "no_ask": kalshi._cents(m.get("no_ask_dollars")),
                    "volume": kalshi._f(m.get("volume_fp")) or 0.0,
                    "close": kalshi._parse_time(m.get("close_time")),
                    "expiry": kalshi._parse_time(m.get("expected_expiration_time"))}
            if i % 4 == 3:
                time.sleep(0.25)
        # home then away from the event ticker's tail (see the module docstring)
        for ev in out.values():
            tail = _EVT_RE.match(ev["event"]).group(5)
            codes = [c for c in ev["markets"] if c != "TIE"]
            first = next((c for c in codes if tail.startswith(c)), None)
            if first and len(codes) == 2:
                ev["home_code"] = first
                ev["away_code"] = next(c for c in codes if c != first)
        return out
    return racing._cached(("soccer_kalshi",), 240, build) or {}


# ---- Kalshi both teams to score ------------------------------------------------
# Kalshi lists a full-match BTTS market beside most GAME events: series
# KX<league>BTTS, the same event tail as the GAME event, one market
# "<event>-BTTS" whose YES pays when both sides score in 90 minutes plus
# stoppage (the rules; no extra time) -- the model's own window. The tickers
# are known from the GAME events, so the board asks for them by name, a
# hundred a call (10 calls on 2026-10-17 for 968 matches; one call per BTTS
# series was 88 calls and read 429 from Kalshi). 452 of the 968 had one; 389
# of those were two-sided books within 10c, the rest 35-50c wide or empty.
BTTS_MAX_SPREAD = 10.0
# research/soccer_btts_validation.py. Rule A (written first): the RAW grid
# P(BTTS) did WORSE than the league's own BTTS rate on held-out matches --
# 2025 +0.00381 +/- 0.00125 log loss a match, 2026 +0.00632 +/- 0.00140 --
# too extreme both ways (said 26% -> 36% happened; 73% -> 66%): treating the
# two sides' goals as independent overstates how much BTTS varies. Rule B
# (written after A failed, so held to both years): the figure recalibrated
# toward the league rate, p = logistic(a + b logit(raw) + c logit(league)),
# (a, b, c) fitted on 2024 alone, beat the league rate on 2025 (-0.00270 +/-
# 0.00053) AND 2026 (-0.00207 +/- 0.00060), calibrated within about a point
# (said 54% -> 55%). That figure is the card's and carries the edge. Against
# Kalshi it is untested: the live record (bucket soccer_btts) is the test.
BTTS_RECAL = (0.03561, 0.42932, 0.48344)
BTTS_EDGE = True
# A league with no BTTS rate in the artifact (an artifact built before the
# rate was stored) uses the measured overall rate (2025 51.4%, 2026 52.6%).
BTTS_GLOBAL_FALLBACK = 0.52


def btts_ticker(ev):
    return f"{ev['series'][:-4]}BTTS-{ev['event'].split('-', 1)[1]}-BTTS"


def btts_markets(evs):
    """{BTTS ticker: {ticker, yes_bid, yes_ask, no_bid, no_ask, volume,
    close}} for the GAME events' BTTS markets. Cached 4 minutes."""
    def build():
        want = sorted({btts_ticker(e) for e in evs.values() if e.get("series", "").endswith("GAME")})
        out = {}
        for i in range(0, len(want), 100):
            chunk = want[i:i + 100]
            try:
                d = kalshi._get_json(f"{kalshi.BASE}/markets?tickers={','.join(chunk)}&limit=200", timeout=25)
            except Exception as e:
                errlog.note("SOC-btts", e)
                continue
            for m in d.get("markets") or []:
                out[m.get("ticker")] = {
                    "ticker": m.get("ticker"),
                    "yes_bid": kalshi._cents(m.get("yes_bid_dollars")),
                    "yes_ask": kalshi._cents(m.get("yes_ask_dollars")),
                    "no_bid": kalshi._cents(m.get("no_bid_dollars")),
                    "no_ask": kalshi._cents(m.get("no_ask_dollars")),
                    "volume": kalshi._f(m.get("volume_fp")) or 0.0,
                    "close": kalshi._parse_time(m.get("close_time"))}
            time.sleep(0.3)
        return out
    return racing._cached(("soccer_btts",), 240, build) or {}


def btts_row(bm, model, pregame):
    """The card's BTTS block: Kalshi's book, the model's figure, and an edge
    only when the figure has earned one (BTTS_EDGE) on a readable pre-game
    book."""
    b = dict(bm)
    ya, yb = bm.get("yes_ask"), bm.get("yes_bid")
    b["readable"] = bool(ya is not None and yb and 0 < ya < 100 and ya - yb <= BTTS_MAX_SPREAD)
    b["mid"] = round((ya + yb) / 2.0, 1) if b["readable"] else None
    if model and model.get("btts") is not None:
        p = model["btts"]
        b["model_pct"] = round(100 * p, 1)
        if not pregame:
            b["live"] = True
        elif BTTS_EDGE and b["readable"]:
            na = bm.get("no_ask")
            b["edge_yes"] = round(100 * p - ya - kalshi.fee_for_market(bm["ticker"], ya), 1)
            if na and 0 < na < 100:
                b["edge_no"] = round(100 * (1 - p) - na - kalshi.fee_for_market(bm["ticker"], na), 1)
    b["edge_shown"] = BTTS_EDGE
    return b


# ---- ESPN schedule and matching ----------------------------------------------
def _dk_odds(od):
    """DraftKings' three-way line as ESPN's scoreboard carries it: the home
    and away prices sit under moneyline.{home,away}.close.odds as strings
    ("+115", "-190"), the draw under drawOdds.moneyLine as a number. None
    when any of the three is missing (ESPN shows the draw alone on some
    leagues)."""
    if not od:
        return None
    ml = od.get("moneyline") or {}

    def side(k):
        v = ((ml.get(k) or {}).get("close") or {}).get("odds")
        if v in (None, "", "EVEN"):
            return 100.0 if v == "EVEN" else None
        return kalshi._f(str(v).replace("+", ""))
    out = {"h": side("home"), "a": side("away"), "d": kalshi._f((od.get("drawOdds") or {}).get("moneyLine"))}
    return out if all(v is not None for v in out.values()) else None


def upcoming(date):
    """Every ESPN soccer match on one UTC date across all leagues: [{id,
    league_id, date, h: {id, name, short, abbr}, a: {...}, odds: {h, d, a}
    American moneylines or None, state, detail, scores}]. Cached 10 min."""
    ymd = date.replace("-", "")

    def build():
        d = _espn(f"{_SITE}/all/scoreboard?dates={ymd}&limit=1000")
        out = []
        for e in d.get("events") or []:
            c = (e.get("competitions") or [{}])[0]
            cs = c.get("competitors") or []
            h = next((x for x in cs if x.get("homeAway") == "home"), None)
            a = next((x for x in cs if x.get("homeAway") == "away"), None)
            if not h or not a:
                continue
            uid = e.get("uid") or ""
            lid = uid.split("~")[1][2:] if "~l:" in uid else None
            od = (c.get("odds") or [{}])[0]
            st = ((c.get("status") or {}).get("type") or {})

            def team(x):
                t = x.get("team") or {}
                return {"id": str(t.get("id")), "name": t.get("displayName"),
                        "short": t.get("shortDisplayName"), "abbr": t.get("abbreviation"),
                        "score": x.get("score")}
            out.append({"id": e.get("id"), "league_id": lid, "date": e.get("date"),
                        "neutral": bool(c.get("neutralSite")),
                        "h": team(h), "a": team(a),
                        "odds": _dk_odds(od),
                        "state": st.get("state"), "detail": st.get("shortDetail")})
        return out
    return racing._cached(("soccer_upcoming", ymd), 600, build) or []


_STOP = {"fc", "cf", "sc", "cd", "ac", "afc", "club", "de", "del", "da", "do", "la", "el",
         "the", "us", "ss", "as", "fk", "sk", "bk", "if", "ff", "sv", "vfb", "vfl", "tsg",
         "fsv", "ca", "ud", "sd", "rc", "cs", "ks", "nk", "hk", "sl", "gd", "sg", "ii", "u",
         "w", "reg", "time", "ad", "cp", "cr", "ec", "se", "aa", "ii"}
# Kalshi spellings ESPN does not share (lower case, after the "Reg Time:" strip).
_ALIAS = {"copenhagen": "kobenhavn", "crvena zvezda": "red star belgrade",
          "los angeles g": "la galaxy", "los angeles f": "lafc", "madeira": "maritimo",
          "guimaraes": "vitoria guimaraes", "athletic club sjdr": "athletic",
          "america fc": "america mineiro",
          "aarhus": "agf", "al suqoor": "neom", "panaitolikos": "panetolikos",
          "uni craiova": "csu craiova", "ic escaldes": "inter d'escaldes",
          "salgueiros": "sagueiros", "tractor tabriz": "traktor sazi", "cr brasil": "crb",
          "mito h": "mito hollyhock", "fagiano o": "fagiano okayama", "hamkam": "hamarkameratene",
          "bodoe/glimt": "bodo/glimt", "gak": "grazer ak", "alacranes": "durango",
          "defensores": "defensores de belgrano", "lourosa": "lusitania", "cajamarca": "fc cajamarca"}


_TRANS = str.maketrans({"ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "ß": "ss", "ð": "d", "Ð": "d",
                        "þ": "th", "Þ": "th", "ł": "l", "Ł": "l", "đ": "d", "Đ": "d", "œ": "oe", "Œ": "oe"})


def _tokens(s):
    # NFKD strips accents but not the letters that are not accented forms
    # (ø, æ, ß, ł ...): "København" came out "kbenhavn" and never met
    # Kalshi's "Copenhagen" alias
    s = unicodedata.normalize("NFKD", (s or "").translate(_TRANS)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"^reg time:\s*", "", s).replace(".", "").strip()     # "F.C." -> "fc", a stop word
    s = _ALIAS.get(s, s)
    s = s.replace("&", " and ").replace("-", " ").replace("/", " ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return [t for t in s.split() if t not in _STOP]


def _containment(kt, ct):
    """How much of the shorter name the longer one holds, 0-1: a token
    counts whole on equality, 0.8 on a four-letter prefix ("Hamarkameratene"
    / "HamKam"), 0.6 on an initial ("Tokyo V" / "Tokyo Verdy")."""
    if not kt or not ct:
        return 0.0
    inter = 0.0
    for a in kt:
        for b in ct:
            if a == b:
                inter += 1
                break
            if len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a)):
                inter += 0.8
                break
            if (len(a) <= 2 and len(b) > 2 and b.startswith(a)) or (len(b) <= 2 and len(a) > 2 and a.startswith(b)):
                inter += 0.6
                break
    return inter / min(len(kt), len(ct))


# The ticker's order is home-first on all but two of 640 events, so ESPN
# overrides it only when the names say so by a clear margin: "Tokyo V" v
# "Tokyo" scored the wrong way round by 0.2 on token containment alone.
_FLIP_MARGIN = 0.5


def name_score(kalshi_name, code, team):
    """How well a Kalshi team name (and its ticker code) fits an ESPN team:
    token containment over the display, short and abbreviated names, 0-1."""
    kt = _tokens(kalshi_name)
    best = 0.0
    for cand in (team.get("name"), team.get("short")):
        best = max(best, _containment(kt, _tokens(cand)))
    if code and (team.get("abbr") or "").upper() == code.upper():
        best = max(best, 0.9)
    return best


def match_event(ev, slug_ids, by_date):
    """The ESPN match for a Kalshi event, or None. Candidates are the ESPN
    matches of the mapped league on the ticker's date +-1 day (Kalshi's date
    is Eastern, ESPN's UTC). Accepted when both teams fit, or one fits
    outright and the other is the only remaining opponent that day."""
    slug = ESPN_LEAGUE.get(ev["series"])
    if not slug or not ev.get("date") or not ev.get("home_code"):
        return None
    lids = slug_ids.get(slug) or set()
    base = datetime.date.fromisoformat(ev["date"])
    hm = ev["markets"][ev["home_code"]]
    am = ev["markets"][ev["away_code"]]
    best, second = (0.0, None), 0.0
    for dd in (-1, 0, 1):
        for e in by_date.get((base + datetime.timedelta(days=dd)).isoformat(), []):
            if e["league_id"] not in lids:
                continue
            # both orientations: the ticker's order is home-first on all but
            # a couple of neutral-ground cup ties, and those still match
            sh = name_score(hm["name"], ev["home_code"], e["h"])
            sa = name_score(am["name"], ev["away_code"], e["a"])
            fh = name_score(hm["name"], ev["home_code"], e["a"])
            fa = name_score(am["name"], ev["away_code"], e["h"])
            if fh + fa > sh + sa:
                sh, sa = fh, fa
            s = sh + sa
            if s > best[0]:
                second = best[0]
                best = (s, e, sh, sa)
            elif s > second:
                second = s
    if best[1] is None:
        return None
    s, e, sh, sa = best
    if s >= 1.2 and second < s - 0.3:
        return e
    if (sh >= 0.9 or sa >= 0.9) and s >= 0.9 and second < 0.9:
        return e
    return None


# ---- prices -------------------------------------------------------------------
def _mid(m):
    a, b = m.get("yes_ask"), m.get("yes_bid")
    if a is None or not (0 < a < 100):
        return None
    return (a + b) / 2.0 if (b is not None and 0 < b < 100) else a


def fair(markets, home_code, away_code):
    """Kalshi's own three-way probabilities with the overround stripped:
    {home, draw, away} as 0-1 floats, or None when a side is unquoted."""
    mids = [_mid(markets.get(c) or {}) for c in (home_code, "TIE", away_code)]
    if any(v is None for v in mids):
        return None
    tot = sum(mids)
    return {"home": mids[0] / tot, "draw": mids[1] / tot, "away": mids[2] / tot}


def _american_prob(ml):
    if ml is None:
        return None
    ml = float(ml)
    return 100.0 / (ml + 100.0) if ml > 0 else -ml / (-ml + 100.0)


def dk_fair(odds):
    """DraftKings' three-way line off ESPN, de-vigged, or None."""
    if not odds:
        return None
    ps = [_american_prob(odds.get(k)) for k in ("h", "d", "a")]
    if any(p is None for p in ps):
        return None
    tot = sum(ps)
    return {"home": ps[0] / tot, "draw": ps[1] / tot, "away": ps[2] / tot}


# ---- the board ----------------------------------------------------------------
_cache = {}


def _recent_and_meetings(rows, model):
    """({team: last _LAST matches, latest first}, {(lo, hi): every meeting,
    latest first}) from the fit's own window, each match carrying the
    current fit's expected goals at its venue: the residuals the form and
    head-to-head terms read (exactly as research/soccer_form_validation.py
    computes them)."""
    recent, meetings = {}, {}
    for m in reversed(rows):
        day, comp, h, a, hg, ag, neu = m
        lh, la = rates(model, h, a, comp, neu)
        rec = (day, comp, h, a, hg, ag, round(lh, 4), round(la, 4), bool(neu))
        for t in (h, a):
            lst = recent.setdefault(t, [])
            if len(lst) < _LAST:
                lst.append(rec)
        meetings.setdefault((min(h, a), max(h, a)), []).append(rec)
    return recent, meetings


def form_residual(recent, team, n):
    """(r_att, r_def) over the team's last n matches: log((goals + prior) /
    (expected + prior)) and log((expected conceded + prior) / (conceded +
    prior)). Positive is good on both."""
    gf = ga = xf = xa = 0.0
    for day, comp, h, a, hg, ag, lh, la, neu in (recent.get(team) or [])[:n]:
        if h == team:
            gf += hg; ga += ag; xf += lh; xa += la
        else:
            gf += ag; ga += hg; xf += la; xa += lh
    return (math.log((gf + FORM_PRIOR) / (xf + FORM_PRIOR)),
            math.log((xa + FORM_PRIOR) / (ga + FORM_PRIOR)))


def h2h_residual(meetings, home, away):
    """(r_home, r_away, n): each side's goals in the past meetings against
    its expected goals under the current fit, log((goals + prior) /
    (expected + prior)), any venue and competition in the window."""
    gh = ga = xh = xa = 0.0
    ms = meetings.get((min(home, away), max(home, away))) or []
    for day, comp, h, a, hg, ag, lh, la, neu in ms:
        if h == home:
            gh += hg; ga += ag; xh += lh; xa += la
        else:
            gh += ag; ga += hg; xh += la; xa += lh
    return (math.log((gh + FORM_PRIOR) / (xh + FORM_PRIOR)),
            math.log((ga + FORM_PRIOR) / (xa + FORM_PRIOR)), len(ms))


def build_artifact(slugs, asof=None):
    """Everything the board prices with, refit every six hours in a child
    (refit_main): the long fit, the short fit when the form term is the
    blend, every team's last _LAST matches and every pair's meetings with
    the fit's expected goals, and the team names."""
    asof = asof or clock.today_et()
    shots = {}
    rows, names = history(slugs, asof, shots=shots)
    t0 = time.time()
    # the fit reads pseudo-goals (SHOTS_W); the form panel, the meetings and
    # the BTTS base rate read the real goals
    fit_rows = pseudo_rows(rows, shots)
    model = fit(fit_rows, asof.toordinal())
    short = (fit(fit_rows, asof.toordinal(), xi=FORM_PARAM) if (FORM_KIND == "A" and W_FORM) else None)
    recent, meetings = _recent_and_meetings(rows, model)
    return {"asof": asof.isoformat(), "slugs": sorted(set(slugs)), "model": model, "short": short,
            "recent": recent, "meetings": meetings, "names": names,
            "btts_base": btts_rates(rows, asof.toordinal()),
            "shots_w": SHOTS_W, "n_shots": sum(1 for r in rows if r[:4] in shots),
            "n_rows": len(rows), "fit_s": round(time.time() - t0, 1)}


def btts_rates(rows, asof_ord):
    """{"comp": {competition: rate}, "global": rate}: each competition's
    decayed both-teams-score frequency over the fit's own results (weights
    exp(-XI x age), Laplace +1/+2) -- research/soccer_btts_validation.py's
    benchmark and the recalibration's second input, built the same way."""
    acc, g = {}, [0.0, 0.0]
    for r in rows:
        day, comp, hg, ag = r[0], r[1], r[4], r[5]
        w = math.exp(-XI * (asof_ord - day))
        y = 1.0 if (hg > 0 and ag > 0) else 0.0
        a_ = acc.setdefault(comp, [0.0, 0.0])
        a_[0] += w * y
        a_[1] += w
        g[0] += w * y
        g[1] += w
    return {"comp": {c: (v[0] + 1.0) / (v[1] + 2.0) for c, v in acc.items()},
            "global": (g[0] / g[1]) if g[1] else BTTS_GLOBAL_FALLBACK}


def btts_cal(raw, base):
    """The shipped P(BTTS): the grid's figure recalibrated toward the
    league's rate (BTTS_RECAL)."""
    def lg(p):
        p = min(1 - 1e-6, max(1e-6, p))
        return math.log(p / (1 - p))
    a, b, c = BTTS_RECAL
    return 1.0 / (1.0 + math.exp(-(a + b * lg(raw) + c * lg(base))))


def refit_main(params):
    """The refit child's entry point: build and save the artifact. Niced,
    out of the web worker -- ~200 ESPN year files and a fit is the kind of
    work that took the instance down when it ran inline (CLAUDE.md)."""
    import deep_cache
    art = build_artifact(params.get("slugs") or [])
    if art["model"]["n"]:
        deep_cache.save(_ART_KEY, art)
        return 0
    return 1


_ART_KEY = "soccer_model"
_ART_TTL = 6 * 3600
_art_mem = {"mtime": None, "art": None}
_refit_try = [0.0]


def _load_artifact():
    import deep_cache
    age = deep_cache.age(_ART_KEY)
    if age is None:
        return None, None
    stamp = round(time.time() - age)
    if _art_mem["mtime"] != stamp:
        art, _ts = deep_cache.load(_ART_KEY)
        _art_mem.update(mtime=stamp, art=art)
    return _art_mem["art"], age


def _refit_child(slugs):
    """Run refit_main in a niced child under the app's one-heavy-build gate.
    True when a fresh artifact was written."""
    import deep_cache
    import json as _json
    import os
    import subprocess
    import sys
    if not deep_cache.HEAVY_BUILD.acquire(timeout=900):
        errlog.note("SOC-refit", msg="another heavy build held the gate for 15 min; refit skipped")
        return False
    try:
        out = subprocess.run(
            [sys.executable, "-c", "import os; os.nice(10)\n"
             "import sys, json, soccer; sys.exit(soccer.refit_main(json.loads(sys.stdin.read())))"],
            input=_json.dumps({"slugs": sorted(set(slugs))}).encode(),
            cwd=os.path.dirname(os.path.abspath(__file__)), stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=1200)
        if out.returncode != 0:
            errlog.note("SOC-refit", msg=f"child rc={out.returncode}: "
                        + (out.stderr or b"").decode(errors="replace")[-300:])
            return False
        return True
    except Exception as e:
        errlog.note("SOC-refit", e)
        return False
    finally:
        deep_cache.HEAVY_BUILD.release()


def model_artifact(slugs):
    """The artifact the board prices with: refit in a child when missing,
    from a previous day, six hours old, or missing a league the board now
    needs -- at most one attempt every 30 minutes. A failed refit keeps the
    last good artifact (stale beats none); with none, the board shows the
    price alone."""
    art, age = _load_artifact()
    today = clock.today_et().isoformat()
    need = set(s for s in slugs if s)
    stale = (art is None or art.get("asof") != today or age > _ART_TTL
             or not need <= set(art.get("slugs") or []) or "btts_base" not in art
             or art.get("shots_w") != SHOTS_W)
    if stale and time.time() - _refit_try[0] > 1800:
        _refit_try[0] = time.time()
        if _refit_child(need):
            art, age = _load_artifact()
    return art


def _wrap(model_or_art):
    """An artifact, or a bare fit wrapped as one (no form, no meetings)."""
    if model_or_art and "model" in model_or_art:
        return model_or_art
    return {"model": model_or_art or _empty(), "short": None, "recent": {}, "meetings": {}, "names": {}}


def _probs(lh, la):
    return outcome(grid(lh, la, RHO))


def predict_match(art, home, away, comp, neutral=False):
    """predict() plus the form and head-to-head terms at their shipped
    weights, and what the card shows of both: each side's last five, the
    past meetings, and how far each term moved the three outcomes."""
    art = _wrap(art)
    model = art["model"]
    lh0, la0 = rates(model, home, away, comp, neutral)
    llh, lla = math.log(lh0), math.log(la0)
    base = _probs(lh0, la0)
    recent, meetings, names = art.get("recent") or {}, art.get("meetings") or {}, art.get("names") or {}
    # form
    if FORM_KIND == "A":
        sm = art.get("short")
        if W_FORM and sm:
            sh, sa = rates(sm, home, away, comp, neutral)
            llh += W_FORM * (math.log(sh) - math.log(lh0))
            lla += W_FORM * (math.log(sa) - math.log(la0))
        fh = fa = None
    else:
        fh = form_residual(recent, home, FORM_PARAM)
        fa = form_residual(recent, away, FORM_PARAM)
        if W_FORM:
            llh += W_FORM * (fh[0] - fa[1])
            lla += W_FORM * (fa[0] - fh[1])
    after_form = _probs(math.exp(llh), math.exp(lla))
    # head-to-head
    rh, ra, nm = h2h_residual(meetings, home, away)
    if W_H2H and nm:
        llh += W_H2H * rh
        lla += W_H2H * ra
    lh, la = math.exp(llh), math.exp(lla)
    g = grid(lh, la, RHO)
    ph, pd, pa = outcome(g)
    top = sorted(g.items(), key=lambda kv: -kv[1])[:5]

    def last5(team):
        out = []
        for day, c, h, a, hg, ag, xh, xa, neu in (recent.get(team) or [])[:5]:
            home_side = h == team
            gf, ga = (hg, ag) if home_side else (ag, hg)
            out.append({"date": datetime.date.fromordinal(day).isoformat(), "comp": c,
                        "opp": names.get(a if home_side else h, "?"), "venue": "N" if neu else ("H" if home_side else "A"),
                        "gf": gf, "ga": ga, "res": "W" if gf > ga else "D" if gf == ga else "L"})
        return out
    mlist = []
    for day, c, h, a, hg, ag, xh, xa, neu in (meetings.get((min(home, away), max(home, away))) or [])[:6]:
        mlist.append({"date": datetime.date.fromordinal(day).isoformat(), "comp": c,
                      "home": names.get(h, "?"), "away": names.get(a, "?"), "hg": hg, "ag": ag})

    def moved(a_, b_):
        return {"home": round(100 * (b_[0] - a_[0]), 1), "draw": round(100 * (b_[1] - a_[1]), 1),
                "away": round(100 * (b_[2] - a_[2]), 1)}
    bb = art.get("btts_base") or {}
    b_base = (bb.get("comp") or {}).get(comp, bb.get("global", BTTS_GLOBAL_FALLBACK))
    b_raw = sum(p for (x, y), p in g.items() if x > 0 and y > 0)
    return {"p_home": round(ph, 4), "p_draw": round(pd, 4), "p_away": round(pa, 4),
            "exp_home": round(lh, 2), "exp_away": round(la, 2),
            "over25": round(sum(p for (x, y), p in g.items() if x + y > 2.5), 3),
            # the shipped BTTS figure is the recalibrated one (BTTS_RECAL);
            # the grid's own and the league's rate ride along
            "btts": round(btts_cal(b_raw, b_base), 3), "btts_raw": round(b_raw, 3),
            "btts_league": round(b_base, 3),
            "scorelines": [{"score": f"{x}-{y}", "pct": round(100 * p, 1)} for (x, y), p in top],
            "known": {"home": home in model["att"], "away": away in model["att"]},
            "base": {"home": round(base[0], 4), "draw": round(base[1], 4), "away": round(base[2], 4)},
            "form": {"weight": W_FORM, "kind": FORM_KIND, "n": FORM_PARAM if FORM_KIND == "B" else None,
                     "home": last5(home), "away": last5(away),
                     "r_home": [round(v, 3) for v in fh] if fh else None,
                     "r_away": [round(v, 3) for v in fa] if fa else None,
                     "moved": moved(base, after_form)},
            "h2h": {"weight": W_H2H, "n": nm, "meetings": mlist,
                    "r_home": round(rh, 3), "r_away": round(ra, 3),
                    "moved": moved(after_form, (ph, pd, pa))}}


def board():
    import boardshare
    return boardshare.nonblocking("soccer_board", 600, _cache, ("soccer_board",),
                                  _build_board, "SOC-board-build")


def _build_board():
    evs = kalshi_events()
    if not evs:
        return None
    slugs = sorted({ESPN_LEAGUE.get(e["series"]) for e in evs.values() if ESPN_LEAGUE.get(e["series"])})
    art = model_artifact(slugs)
    model = (art or {}).get("model") or _empty()
    dates = sorted({e["date"] for e in evs.values() if e.get("date")})
    by_date = {}
    for d in dates:
        for dd in (-1, 0, 1):
            day = (datetime.date.fromisoformat(d) + datetime.timedelta(days=dd)).isoformat()
            if day in by_date:
                continue
            by_date[day] = upcoming(day)
    # the league id of every ESPN match seen -> the slug: learned from the
    # leagues we fetch results for (their events carry the same ids)
    slug_ids = _slug_ids(by_date)
    try:
        btts = btts_markets(evs)
    except Exception as e:
        errlog.note("SOC-btts-board", e)
        btts = {}
    games, log_rows, dk_rows, btts_rows = [], [], [], []
    for ev in sorted(evs.values(), key=lambda e: (e.get("date") or "", e["event"])):
        row = _game_row(ev, art if art else model, slug_ids, by_date, btts=btts)
        if not row:
            continue
        games.append(row)
        # Only a match that has not kicked off is logged: the price beside
        # the prediction is the benchmark the record grades the model
        # against, and a live price carries the match (CLAUDE.md: never let
        # in-game information touch a number that claims to be pre-game).
        if not row.get("pregame"):
            continue
        # the BTTS figure goes on the record in its own bucket against
        # Kalshi's mid on a readable book, so the live record can re-judge
        # a figure the held-out test turned down
        # the kickoff rides as the fifth element: the server logs a carried
        # row only while its match is still ahead (predlog.carry)
        ko = _ko_ts(row.get("kickoff"))
        b = row.get("btts")
        if b and b.get("model_pct") is not None and b.get("readable"):
            btts_rows.append((b["ticker"], b["model_pct"] / 100.0, b.get("close"), b["mid"] / 100.0, ko))
        for side in ("home", "draw", "away"):
            mk = row[side]["kalshi"]
            p = (row.get("model") or {}).get(f"p_{side}")
            if mk and mk.get("ticker") and p and row.get("fair"):
                log_rows.append((mk["ticker"], p, mk.get("close"), row["fair"][side], ko))
                if row.get("dk"):
                    dk_rows.append((mk["ticker"], row["dk"][side], mk.get("close"), row["fair"][side], ko))
    if log_rows or btts_rows:
        try:
            import predlog
            predlog.init_db()
            if log_rows:
                predlog.log_many("soccer", log_rows)
            if dk_rows:
                predlog.log_shadow("soccer_dk", dk_rows)
            if btts_rows:
                predlog.log_many("soccer_btts", btts_rows)
        except Exception as e:
            errlog.note("SOC-predlog", e)
        # the same rows beside the board: a PC build's only way into the record
        import predlog
        predlog.carry("soccer", [("soccer", log_rows), ("soccer_dk", dk_rows), ("soccer_btts", btts_rows)])
    n_btts = sum(1 for g in games if g.get("btts"))
    # how spread out the BTTS figure is beside Kalshi's, over this build's
    # readable books: the recalibration that beat the league average is
    # FLATTER than the market (2026-10-17: SD 6.1 points against Kalshi's 9.1
    # over 284 matches, correlation 0.66), so its largest edges sit where
    # Kalshi is most extreme -- the place a flatter model is most likely the
    # one that is wrong. Printed beside the edges; the live record (bucket
    # soccer_btts) is what can settle which of the two is right.
    pairs = [(g["btts"]["model_pct"], g["btts"]["mid"]) for g in games
             if g.get("btts") and g["btts"].get("readable") and g["btts"].get("model_pct") is not None]
    btts_spread = None
    if len(pairs) >= 30:
        def _sd(v):
            m_ = sum(v) / len(v)
            return (sum((x - m_) ** 2 for x in v) / len(v)) ** 0.5
        btts_spread = {"n": len(pairs), "model_sd": round(_sd([p for p, _ in pairs]), 1),
                       "kalshi_sd": round(_sd([k for _, k in pairs]), 1)}
    n_model = sum(1 for g in games if g.get("model"))
    n_heavy = sum(1 for g in games if g.get("heavy"))
    return {"built_ts": int(time.time()), "n_games": len(games), "n_model": n_model,
            "n_heavy": n_heavy, "n_btts": n_btts, "btts_edge": BTTS_EDGE, "btts_spread": btts_spread,
            "games": games,
            "fit": {"n": model.get("n"), "asof": (art or {}).get("asof"), "fit_s": (art or {}).get("fit_s"),
                    "teams": len(model.get("att") or {}), "xi": XI, "kappa": KAPPA, "rho": RHO,
                    "form": {"kind": FORM_KIND, "param": FORM_PARAM, "weight": W_FORM}, "h2h_weight": W_H2H},
            "leagues": sorted({g["league"] for g in games}),
            "heavy_gap": HEAVY_GAP,
            # what the headline chance rests on (research/soccer_market_validation.py)
            "model_weight": MODEL_WEIGHT, "shots_w": SHOTS_W,
            "market_test": {"leagues": 27, "matches": 24841, "worse_2025": 0.0192, "se_2025": 0.0018,
                            "worse_2026": 0.0219, "se_2026": 0.0020},
            # the out-of-sample calibration of the leg the card leads with
            # (P the underdog does not win) on the heavy-underdog subset, 2025
            # and 2026 to date, weekly refits (research/reports/soccer_form.md):
            # about a point and a half high on 2025, under a point on 2026
            "calibration": {"heavy": [{"pred": 0.765, "hit": 0.749, "n": 1633}, {"pred": 0.847, "hit": 0.832, "n": 2948},
                                      {"pred": 0.927, "hit": 0.921, "n": 833}],
                            "heavy_2026": [{"pred": 0.765, "hit": 0.755, "n": 1314}, {"pred": 0.846, "hit": 0.837, "n": 2392},
                                           {"pred": 0.924, "hit": 0.936, "n": 612}]},
            "note": ("Every Kalshi soccer match. The model is a Dixon-Coles goals model: each "
                     "team's attack and defence from two years of results (goals, and a quarter "
                     "weight on shots on target) weighted to the recent, a home advantage per "
                     "competition, the exact scoreline grid. Against the closing market it lost "
                     "in every league tested, so each card's headline chance is Kalshi's own "
                     "de-vigged price and the model's figure sits beside it. Recent form and "
                     "head-to-head are shown on every card but carry no weight: neither improved "
                     "held-out predictions. NO on the underdog pays on the favourite's win and the tie.")}


def _league_ids(slug):
    """The league ids ESPN's all-leagues feed uses for one mapped league,
    from that league's own scoreboard. None when the fetch failed."""
    try:
        d = _espn(f"{_SITE}/{slug}/scoreboard", timeout=20)
    except Exception as e:
        errlog.note("SOC-league-id", e, path=slug)
        return None
    ids = set()
    for e in d.get("events") or []:
        uid = e.get("uid") or ""
        if "~l:" in uid:
            ids.add(uid.split("~")[1][2:])
    lg = (d.get("leagues") or [{}])[0]
    if lg.get("id"):
        ids.add(str(lg["id"]))
    return ids or None


# A mapped league missing from the join is looked up again after this long.
# The join is cached for a day, and one failed fetch took the NWSL (7
# matches) and the French women's league (6) off the model for the day
# (2026-10-17): every match read "could not find this match on ESPN's
# schedule". Only the missing leagues are re-fetched.
_SLUG_RETRY = 3600
_slug_retry_at = {}


def _slug_ids(by_date):
    """{slug: {league ids}} for the mapped leagues, learned by looking each
    mapped league's own scoreboard up for one date it has a match on. ESPN's
    all-leagues feed carries the league ID, its per-league feed the slug;
    the join is learned once a day and cached."""
    def build():
        out = {}
        for slug in sorted(set(ESPN_LEAGUE.values()) - {None}):
            ids = _league_ids(slug)
            if ids:
                out[slug] = ids
        return out or None
    got = racing._cached(("soccer_slug_ids",), 24 * 3600, build) or {}
    now = time.time()
    for slug in sorted(set(ESPN_LEAGUE.values()) - {None} - set(got)):
        if now - _slug_retry_at.get(slug, 0) < _SLUG_RETRY:
            continue
        _slug_retry_at[slug] = now
        ids = _league_ids(slug)
        if ids:
            got[slug] = ids              # the cached dict itself: every later build sees it
    return got


def _ko_ts(s):
    """ESPN's kickoff string as an epoch, None when absent or unreadable."""
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _game_row(ev, art, slug_ids, by_date, now=None, btts=None):
    """One card. `art` is the artifact (or a bare fit, wrapped). A match
    ESPN shows as started or finished keeps its prices and the model's
    pre-game figure but no edge: the model knows nothing of the match in
    progress, and the live NO ask does."""
    mk = ev["markets"]
    hc, ac = ev.get("home_code"), ev.get("away_code")
    if not hc or not ac or "TIE" not in mk:
        return None
    hm, am, tm = mk[hc], mk[ac], mk["TIE"]
    e = match_event(ev, slug_ids, by_date)
    slug = ESPN_LEAGUE.get(ev["series"])
    if e:
        # ESPN's home side is the venue's truth; the ticker's order agreed
        # with it on 638 of 640 events and the two it did not were cup ties
        # at a neutral ground. The card and the model follow ESPN.
        straight = name_score(hm["name"], hc, e["h"]) + name_score(am["name"], ac, e["a"])
        flipped = name_score(hm["name"], hc, e["a"]) + name_score(am["name"], ac, e["h"])
        if flipped > straight + _FLIP_MARGIN:
            hc, ac = ac, hc
            hm, am = am, hm
    row = {"event": ev["event"], "series": ev["series"], "league": ev["league"], "date": ev["date"],
           "kickoff": e["date"] if e else None,
           "close": tm.get("close"), "expiry": tm.get("expiry"),
           "home": {"code": hc, "name": hm["name"], "kalshi": hm},
           "away": {"code": ac, "name": am["name"], "kalshi": am},
           "draw": {"kalshi": tm},
           "espn": ({"id": e["id"], "home": e["h"]["name"], "away": e["a"]["name"],
                     "state": e["state"], "detail": e["detail"],
                     "home_score": e["h"].get("score"), "away_score": e["a"].get("score")} if e else None),
           "fair": None, "dk": dk_fair(e["odds"]) if e else None}
    # the underdog, by Kalshi's own ask (the owner's filter is in Kalshi terms)
    ha, aa, ta = hm.get("yes_ask"), am.get("yes_ask"), tm.get("yes_ask")
    quoted = all(v is not None and 0 < v < 100 for v in (ha, aa, ta))
    row["book_sum"] = round(ha + aa + ta, 1) if quoted else None
    readable = quoted and (ha + aa + ta) <= MAX_BOOK
    row["readable"] = readable
    if readable:
        row["fair"] = fair(mk, hc, ac)
    # a placeholder book is listed, never read: no underdog, no filter, no edge
    row["quoted"] = quoted = readable
    if quoted:
        dog = "home" if ha < aa else "away"
        dog_ask = min(ha, aa)
        row["dog"] = dog
        row["dog_ask"] = dog_ask
        row["tie_ask"] = ta
        row["heavy"] = dog_ask <= ta - HEAVY_GAP
    else:
        row["dog"] = None
        row["heavy"] = False
    now = time.time() if now is None else now
    ko = _ko_ts(e["date"]) if e else None
    row["pregame"] = bool(e and e.get("state") == "pre" and (ko is None or ko > now))
    if e and slug:
        pred = predict_match(art, e["h"]["id"], e["a"]["id"], slug, neutral=e.get("neutral"))
        pred["weak"] = slug in _WEAK_COMPS
        row["model"] = pred
    else:
        row["model"] = None
        row["why"] = ("ESPN has no feed for this league" if not slug
                      else "could not find this match on ESPN's schedule")
    # the headline chance (MODEL_WEIGHT): pre-game, the pool of the model and
    # Kalshi's de-vigged price; with no readable book, the model's; with no
    # model, Kalshi's. A live match keeps the pre-game blend's ingredients
    # out of it (a live price carries the match), so its chance is the model's.
    m3 = ({"home": row["model"]["p_home"], "draw": row["model"]["p_draw"], "away": row["model"]["p_away"]}
          if row["model"] else None)
    if m3 and row["fair"] and row["pregame"]:
        row["chance"] = headline(m3, row["fair"])
    else:
        row["chance"] = m3 or row["fair"]
    if quoted and row["dog"]:
        dm = hm if row["dog"] == "home" else am
        no_ask = dm.get("no_ask")
        nd = {"ticker": dm.get("ticker"), "no_ask": no_ask,
              "fair_pct": (round(100 * (1 - row["fair"]["home" if row["dog"] == "home" else "away"]), 1)
                           if row["fair"] else None)}
        if row["model"]:
            p_no = 1.0 - row["model"]["p_home" if row["dog"] == "home" else "p_away"]
            nd["model_pct"] = round(100 * p_no, 1)
            if not row["pregame"]:
                nd["live"] = True
            elif no_ask is not None and 0 < no_ask < 100:
                # the edge is the HEADLINE's, not the model's: the model lost
                # to the closing market in every league tested (MODEL_WEIGHT)
                c_no = 1.0 - row["chance"][row["dog"]]
                fee = kalshi.fee_for_market(dm.get("ticker"), no_ask)
                nd["chance_pct"] = round(100 * c_no, 1)
                nd["fee"] = round(fee, 2)
                nd["edge"] = round(100 * c_no - no_ask - fee, 1)
        row["no_dog"] = nd
    bm = (btts or {}).get(btts_ticker(ev))
    row["btts"] = btts_row(bm, row["model"], row["pregame"]) if bm else None
    return row


# ---- quick pick: N legs for one Kalshi combo slip --------------------------------
# The owner types a number and gets that many legs for Kalshi's combo
# builder. One leg per match: legs of one match move together, and Kalshi's
# maker prices that against you (combo_engine.QUOTE_ROOM). Every leg is a
# market the combo accepts (kalshi.combo_events: on 2026-10-17, 189 of the
# 957 soccer GAME events and 109 BTTS events, mostly that day's big
# leagues), on a book that is two-sided and at most QUICK_MAX_SPREAD wide,
# in a match the model prices and that has not kicked off. The default
# window is the owner's own day in Mountain time (clock.mt_date), reaching
# into the next day only when today cannot fill N.
#
# Which legs (the owner's rule, combo_engine.quick_tier): surest first --
# locks (95%+), then 90%+, then 80%+, nothing under -- a leg's tier the lower
# of the model's chance and Kalshi's (the de-vigged three-way price; the BTTS
# mid), and any leg the two put 15 points or more apart is disqualified.
# The model here is unblended, so the comparison is the model's own number.
# Before this rule the chooser was the makers' "likeliest not -EV", whose
# EV leaned on the model wherever it disagreed with Kalshi -- the gap this
# rule now refuses.
QUICK_MAX_SPREAD = 10.0
QUICK_LEAD_S = 300          # a match kicking off inside five minutes is not offered
QUICK_MAX_LEGS = 12


def _book_ok(ask, bid):
    return (ask is not None and bid is not None and 0 < ask < 100 and bid > 0
            and ask - bid <= QUICK_MAX_SPREAD)


def quick_legs(row, eligible, now):
    """(legs, blocked) for one card. `legs`: every leg a combo can take that
    passes the tier rule -- YES and NO on the home win, the draw and the away
    win, YES and NO on both teams scoring -- each with its tier. `blocked`:
    the legs Kalshi would sell at 80%+ that the rule refused because the model
    and Kalshi are 15+ points apart (shown to the owner, never bet). Both
    empty for a match with no model, one that has started or starts inside
    QUICK_LEAD_S, or one in a competition the model is flagged weak in.
    `eligible` is the set of combo-eligible event tickers; empty means the
    feed is down (unknown), and then nothing is excluded for it."""
    import combo_engine
    m = row.get("model")
    if not m or m.get("weak") or not row.get("pregame"):
        return [], []
    ko = _ko_ts(row.get("kickoff"))
    if ko is None or ko < now + QUICK_LEAD_S:
        return [], []
    hn, an = row["home"]["name"], row["away"]["name"]
    out, blocked = [], []

    def add(mk, yes, p, kind, label, kalshi_p, chance=None):
        """`p` the model's own chance (the tier rule's), `chance` the
        headline's (the slip's combined chance and EV; MODEL_WEIGHT)."""
        ask, bid = (mk.get("yes_ask"), mk.get("yes_bid")) if yes else (mk.get("no_ask"), mk.get("no_bid"))
        if not mk.get("ticker") or not _book_ok(ask, bid):
            return
        tier, why = combo_engine.quick_tier(p, kalshi_p)
        c = p if chance is None else chance
        leg = {"ticker": mk["ticker"], "side": "yes" if yes else "no", "kind": kind,
               "label": label, "prob": c, "marg": c, "model_p": p, "price_cents": ask, "tier": tier,
               "kalshi_p": kalshi_p, "event": row["event"], "league": row["league"],
               "match": f"{hn} v {an}", "kickoff": row["kickoff"], "ko_ts": ko}
        if tier:
            out.append(leg)
        elif why == "gap" and kalshi_p >= combo_engine.QUICK_TIERS[-1][0] / 100.0:
            blocked.append(leg)
    if row.get("readable") and (not eligible or row["event"] in eligible):
        fr = row.get("fair") or {}
        ch = row.get("chance") or {}
        for side in ("home", "draw", "away"):
            mk = row[side]["kalshi"]
            p = m.get(f"p_{side}")
            if not mk or p is None:
                continue
            kp = fr.get(side)
            if side == "draw":
                yl, nl = "Draw", f"No draw: {hn} or {an} wins"
            else:
                name, other = (hn, an) if side == "home" else (an, hn)
                yl, nl = f"{name} to win", f"NO {name}: {other} wins or draw"
            cs = ch.get(side)
            add(mk, True, p, side, yl, kp, cs)
            add(mk, False, 1.0 - p, "not_" + side, nl, None if kp is None else 1.0 - kp,
                None if cs is None else 1.0 - cs)
    b = row.get("btts")
    if (b and b.get("readable") and b.get("model_pct") is not None
            and (not eligible or b["ticker"].rsplit("-", 1)[0] in eligible)):
        p, k = b["model_pct"] / 100.0, b["mid"] / 100.0
        add(b, True, p, "btts", "Both teams score", k)
        add(b, False, 1.0 - p, "not_btts", "Not both teams score", 1.0 - k)
    return out, blocked


def quick_combo(board_, n, day=None, now=None, eligible=None, use_btts=True):
    """`n` legs for one Kalshi combo off the cached board, by the tier rule:
    each match's surest leg, the window's matches surest first. `day` (an
    ISO date, Mountain time) pins the window; None takes the owner's today
    and adds the next day only while the window holds fewer than `n`
    matches -- every match of the earlier days is then on the slip and the
    last day fills the rest, surest first. {"error": ...} when no window
    can fill n (with the count, so the page can offer them all)."""
    import combo_engine
    now = time.time() if now is None else now
    n = max(2, min(QUICK_MAX_LEGS, int(n)))
    if eligible is None:
        eligible = kalshi.combo_events()
    by_day, blocked = {}, {}

    def key(l):
        return combo_engine.quick_order(l["model_p"], l["kalshi_p"])
    for row in (board_ or {}).get("games") or []:
        legs, bl = quick_legs(row, eligible, now)
        if not use_btts:
            legs = [l for l in legs if "btts" not in l["kind"]]
            bl = [l for l in bl if "btts" not in l["kind"]]
        d = None
        if legs:
            d = clock.mt_date(legs[0]["ko_ts"]).isoformat()
            by_day.setdefault(d, []).append(min(legs, key=key))
        for l in bl:
            blocked.setdefault(clock.mt_date(l["ko_ts"]).isoformat(), []).append(l)
    today = clock.mt_date(now).isoformat()
    days = sorted(by_day)
    counts = {d: len(by_day[d]) for d in days}
    if day:
        window = [day] if day in by_day else []
    else:
        window = []
        for d in days:
            window.append(d)
            if sum(counts[x] for x in window) >= n:
                break
    n_avail = sum(counts[d] for d in window)
    bl_win = sorted((l for d in (window or ([day] if day else days[:1])) for l in blocked.get(d, [])),
                    key=lambda l: -abs(l["model_p"] - l["kalshi_p"]))
    base = {"n": n, "today": today, "window": window, "days": counts,
            "eligibility_known": bool(eligible), "n_matches": n_avail,
            "rule": {"tiers": [list(t) for t in combo_engine.QUICK_TIERS], "max_gap": combo_engine.QUICK_MAX_GAP},
            "n_blocked": len(bl_win),
            "blocked": [{"match": l["match"], "label": l["label"], "model_pct": round(100 * l["model_p"], 1),
                         "kalshi_pct": round(100 * l["kalshi_p"], 1)} for l in bl_win[:8]]}
    if n_avail < n:
        what = f"on {day}" if day else "in every listed day together"
        return dict(base, error=f"only {n_avail} match{'es' if n_avail != 1 else ''} {what} have a leg at 80%+ "
                                f"that the model and Kalshi agree on (within {combo_engine.QUICK_MAX_GAP:g} points, "
                                f"combo-eligible, readable book, not started); asked for {n}")
    picked = [l for d in window[:-1] for l in by_day[d]]
    picked += sorted(by_day[window[-1]], key=key)[:n - len(picked)]
    picked.sort(key=lambda l: (l["ko_ts"], l["match"]))
    gross = prob = kal = mod = 1.0
    for l in picked:
        gross *= l["price_cents"] / 100.0
        prob *= l["prob"]
        kal *= l["kalshi_p"]
        mod *= l["model_p"]
    cost = combo_engine.combo_cost(gross)
    legs_out = [{"match": l["match"], "league": l["league"], "kickoff": l["kickoff"], "label": l["label"],
                 "kind": l["kind"], "ticker": l["ticker"], "side": l["side"], "ask": l["price_cents"],
                 "tier": l["tier"], "model_pct": round(100 * l["model_p"], 1), "chance_pct": round(100 * l["prob"], 1),
                 "kalshi_pct": round(100 * l["kalshi_p"], 1)} for l in picked]
    item = {"groups": [{"legs": [{"ticker": l["ticker"]}]} for l in picked]}
    ev = prob / cost - 1.0
    return dict(base, legs=legs_out, ev_ok=ev >= 0,
                extended=bool(not day and window and window[-1] != today),
                tiers={t: sum(1 for l in picked if l["tier"] == t) for _c, t in combo_engine.QUICK_TIERS},
                prob_pct=round(100 * prob, 2), kalshi_prob_pct=round(100 * kal, 2),
                model_prob_pct=round(100 * mod, 2), model_weight=MODEL_WEIGHT,
                asks_x=round(1.0 / gross, 2), payout_x=round(1.0 / cost, 2),
                cost_c=round(100 * cost, 2), ev_pct=round(100 * ev, 1),
                kelly_pct=round(100 * combo_engine.kelly(prob, cost), 2),
                cap_x=combo_engine.MAX_PAYOUT_X,
                quote=combo_engine.quote_status(item), quote_room=combo_engine.QUOTE_ROOM)
