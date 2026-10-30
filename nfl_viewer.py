"""The NFL game viewer: one simulated game, play by play.

The owner (2026-10-29), after Toad Sports' TikTok sims: "view the most average
play ... for every game ... just being able to view play by play", then "on
the model screen be able to click or tap, maybe we can be cool and simulate
where the defense players are ... interceptions" -- and "leave out missed
tackles" (the engine has no contact model; the only source is PFR's charting,
display-only under the licence default). Designed and reviewed with a Fable
agent before a line was written; the record is research/reports/nfl_viewer.md.

WHAT IT SHOWS. The deep engine (nfl_deep_sim) plays the matchup N_SIMS times on
the board's own profiles (nfl_game_sim._deep_profiles), each game on its own
seed (game_seed) so any one replays exactly. The viewer opens on the TYPICAL
game: the one nearest the simulations' medians over the betting coordinates
(DIMS: margin, total, each side's net passing and rushing yards, turnovers and
plays), each coordinate scaled by its spread, among the games the side the
median margin favours WON (no ties; a median of 0 is a pick'em and either side
may win). A game stitched from each down's average play would never score or
punt like football -- the mean play is about five yards -- so "most average"
is a whole game. Without the winner rule the nearest game was a favourite's
LOSS once in eight matchups Fable measured, and the owner would read the
viewer's winner as the pick. K_RANDOM more games ("Another sim") are the first
seeds. The per-down table (_Downs) is over all N_SIMS games and answers the
owner's sentence as spoken: each offence's typical call and gain on each down.
The header's probability is the BOARD's deep figure, never a rate of these
games -- one model, one number.

WHO DID IT is drawn after the game, never inside it: the engine's play log
(play_game(log=)) has no players. Each event is told by Sleeper's projections
for the week -- the offence's own convention (nfl_dfs_engine.deal_team): the
starter throws, a carry goes to a back by projected carries (a rushing
touchdown by projected rushing touchdowns), a catch by projected receptions,
a missed target by projected targets less receptions -- and a defender is
named only on an event the engine made certain: the tackle that ends an
in-bounds, non-scoring play, a sack, an interception, the forced fumble and
the recovery on a lost fumble, the returner on a return touchdown. No pass
breakups (an incompletion has no cause in the engine), no missed tackles, no
pressures. The draw has its own generator (the game's seed xor _NAME_SALT), so
the same game always tells the same story and naming can never move a number.
Run direction, pass side and the shotgun come from each team's own mix this
season (nflverse, the matchup store), shrunk toward the league's; pass length
is conditioned on the yards the play gained (a 48-yard completion was never
"short"). Kickoffs and punts are told as the engine plays them -- a spot, a
block, a return touchdown -- never a return, a fair catch or a touchback.

WHERE IT RUNS. Never in a web worker. The PC builds every upcoming game of the
week (pc_build, its boards pass) in a niced child and its boards sync uploads
the pieces; the server builds one game, on a request, in a niced child under
deep_cache.HEAVY_BUILD only when no piece exists or the PC's has aged past its
grace. One piece per game (piece_name), the games inside as JSON text so the
route serves one of them without building the other ten as objects. A game is
never built once it has kicked off, and a piece is served until kickoff +
SHOW_AFTER_KO_S marked as the pre-game simulation it is.
"""
import hashlib
import json
import os
import pickle
import random
import statistics
import time
import zlib

import errlog

SCHEMA = 1                  # the piece's shape; serve() refuses any other
PREFIX = "nflview_"
N_SIMS = 5000               # medians move ~0.2 points at this N (Fable's check); 9.6 s a matchup (8.8-11.6)
K_RANDOM = 10
DIMS = ("margin", "total", "pass_home", "pass_away", "rush_home", "rush_away",
        "to_home", "to_away", "plays_home", "plays_away")
# A piece older than this is rebuilt (twice a day: Sleeper's injury news moves a
# roster inside a day, and Friday's ruled-out starter must leave Sunday's game).
REBUILD_S = 12 * 3600
# Served after kickoff (marked as the pre-game simulation) for this long, then
# refused; boardshare keeps the files (_KEEP_LONG) so a game-day build failure
# never leaves a hole after the day-old sweep.
SHOW_AFTER_KO_S = 4 * 3600
# Measured on week 8's fourteen games (the typical game and ten others, ~170
# events each, names and text): 400-433 KB a piece pickled, 36-39 KB a game as
# JSON. build_week refuses to publish a bigger one (ledgered NFLV-size). The
# route sends ONE game.
PIECE_MAX = 600_000
_NAME_SALT = 0x5EEDF00D
_GAME_TIMEOUT_S = 240       # one game's build in the child, generous for the server's shared core
# A pick'em (median margin 0) prefers a close game: the centre IS 0, and the
# nearest game by distance alone was a 7-point win on week 8's LV @ NYJ under a
# header reading NYJ 53.6% (Fable's build review, m7).
_PICKEM_MARGIN = 3
# Sunday's inactives land ~90 minutes before kickoff: a piece built before
# kickoff - _INACTIVES_S is rebuilt once kickoff is within _LATE_WINDOW_S, so the
# early games are not told with a roster hours old (Fable m6).
_INACTIVES_S = 85 * 60
_LATE_WINDOW_S = 75 * 60
# Only this week's games and next week's are simulated on request.
_WEEKS_AHEAD = 1
# One server build at a time across every gunicorn worker: HEAVY_BUILD is a
# per-process semaphore, and three taps on three games used to start three
# 60 MB children at once (Fable m4).
_SERVER_GATE = "nflview_build"
# Words for the roles a club may have no name for this week (the footnote's
# list and the NFLV-roles ledger note).
_ROLE_WORDS = {"qb": "quarterback", "car": "ball carrier", "rec": "receiver", "miss": "target",
               "tkl": "tackler", "sack": "pass rusher", "int": "interceptor", "k": "kicker"}

# ---- Flavour: measured on nflverse's 2025 regular season -----------------------------
# (scratch measurement, research/reports/nfl_viewer.md section 3). Each team's own
# 2026 mix is shrunk toward these by _FLAVOR_K plays; with no store, every team
# is the league.
_RUN_MIX = (("left end", 0.112), ("left tackle", 0.121), ("left guard", 0.132), ("up the middle", 0.269),
            ("right guard", 0.134), ("right tackle", 0.128), ("right end", 0.104))
_PASS_SIDE = (("left", 0.383), ("middle", 0.208), ("right", 0.409))
# P(nflverse "deep", air yards 15+) for a completion that gained under each bound:
# 0 of 8,468 completions under 15 yards, 27.7% at 15-19, 58.0% at 20-29, 75.9% at 30+.
_DEEP_CMP = ((15, 0.0), (20, 0.277), (30, 0.580), (10 ** 6, 0.759))
_DEEP_INC, _DEEP_INT = 0.299, 0.400
_SG_DROPBACK, _SG_RUN = 0.818, 0.418
_FLAVOR_K = 60.0

_OFF_POS = ("QB", "RB", "WR", "TE")
_IDP_POS = ("DL", "LB", "DB")
# A player under this share of his team's role total is not drawn for it (camp
# bodies with a token projection; keeps the piece's roster to the real rotation).
_MIN_SHARE = 0.02


def piece_name(season, week, home, away):
    return f"{PREFIX}{int(season)}_w{int(week):02d}_{away}_{home}"


def game_seed(season, week, home, away, i):
    """Game i of a matchup. crc32, never hash(): str hashes change per process."""
    return zlib.crc32(f"{int(season)}|{int(week)}|{home}|{away}|{int(i)}".encode())


def summary(g):
    """One finished game's betting coordinates, in DIMS order. Passing is net of
    sacks; turnovers are interceptions plus lost fumbles."""
    h, a = g["h"], g["a"]
    return (h["pts"] - a["pts"], h["pts"] + a["pts"],
            h["pass_yds"] - h["sack_yds"], a["pass_yds"] - a["sack_yds"],
            h["rush_yds"], a["rush_yds"],
            h["int"] + h["fum_lost"], a["int"] + a["fum_lost"],
            h["plays"], a["plays"])


def choose(sums):
    """(index, centre, scale, side, distance) of the typical game among `sums`
    (summary tuples): the medians of every coordinate are the centre, each
    coordinate's population SD its scale; the candidates are the games whose
    margin has the median margin's sign (side +1 home, -1 away), ties never --
    with a median margin of 0 (side 0, a pick'em) any game with a winner, a game
    decided by _PICKEM_MARGIN or fewer first. The nearest candidate by squared
    scaled distance wins; equal distances keep the lower index. Medians, not
    means: turnovers, overtime totals and plays are skewed (Fable: mean and
    median picked the same game in 6 of 8 matchups)."""
    cols = list(zip(*sums))
    centre = [statistics.median(c) for c in cols]
    scale = [statistics.pstdev(c) or 1.0 for c in cols]
    m = centre[0]
    side = (m > 0) - (m < 0)
    best, best_k = None, None
    for i, s in enumerate(sums):
        if s[0] == 0 or (side and (s[0] > 0) != (side > 0)):
            continue
        d = sum(((x - c) / sc) ** 2 for x, c, sc in zip(s, centre, scale))
        k = (0 if side or abs(s[0]) <= _PICKEM_MARGIN else 1, d)
        if best_k is None or k < best_k:
            best, best_k = i, k
    return best, centre, scale, side, (best_k[1] if best_k else None)


# ---- The per-down table (Fable m7) -------------------------------------------------
_PLAYS = ("run", "cmp", "inc", "int", "sack")


def _success(down, dist, yd, td):
    """The standard success rule: 40% of the distance on first down, 60% on
    second, all of it on third and fourth; a touchdown always."""
    if td:
        return True
    need = dist * (0.4 if down == 1 else 0.6 if down == 2 else 1.0)
    return yd >= need


def _hmed(hist):
    """The median of an integer histogram {value: count}."""
    n = sum(hist.values())
    if not n:
        return None
    half, run = (n + 1) / 2.0, 0
    for v in sorted(hist):
        run += hist[v]
        if run >= half:
            return v
    return None


class _Downs:
    """Every offence's decisions down by down over all the simulated games:
    the call (run, dropback, punt, field goal), what the plays gained, how often
    they succeeded, moved the chains, were sacked or picked. Penalties and
    kneel-downs are not calls."""

    def __init__(self):
        self.c = {}

    def _cell(self, side, down):
        k = (side, down)
        c = self.c.get(k)
        if c is None:
            c = self.c[k] = {"n": 0, "dist": 0, "run": 0, "db": 0, "punt": 0, "fg": 0, "att": 0, "cmp": 0,
                             "sack": 0, "int": 0, "succ": 0, "first": 0, "plays": 0, "yds": 0,
                             "h_run": {}, "h_db": {}}
        return c

    def add(self, log):
        for e in log:
            k = e["k"]
            if k in _PLAYS:
                c = self._cell(e["o"], e["d"])
                c["n"] += 1
                c["plays"] += 1
                c["dist"] += e["t"]
                yd = e.get("yd", 0)
                td = bool(e.get("td"))
                c["yds"] += yd
                if k == "run":
                    c["run"] += 1
                    c["h_run"][yd] = c["h_run"].get(yd, 0) + 1
                else:
                    c["db"] += 1
                    c["h_db"][yd] = c["h_db"].get(yd, 0) + 1
                    if k == "sack":
                        c["sack"] += 1
                    else:
                        c["att"] += 1
                        c["cmp"] += k == "cmp"
                        c["int"] += k == "int"
                lost = e.get("to") in ("int", "fum") or e.get("six")
                if not lost and _success(e["d"], e["t"], yd, td):
                    c["succ"] += 1
                if td or e.get("fd"):
                    c["first"] += 1
            elif k in ("punt", "fg"):
                if k == "fg" and e.get("x") and e["d"] < 4:
                    continue            # the kick the clock forced on 1st-3rd down is not a call (Fable m8)
                c = self._cell(e["o"], e["d"])
                c["n"] += 1
                c["dist"] += e["t"]
                c[k] += 1

    def table(self, n_games):
        """{"home": [row for downs 1-4], "away": [...]}, rates in percent."""
        def pct(a, b):
            return round(100.0 * a / b, 1) if b else None
        out = {}
        for side, name in ((0, "home"), (1, "away")):
            rows = []
            for down in (1, 2, 3, 4):
                c = self.c.get((side, down))
                if not c or not c["n"]:
                    rows.append({"down": down, "n": 0})
                    continue
                hall = dict(c["h_run"])
                for v, m in c["h_db"].items():
                    hall[v] = hall.get(v, 0) + m
                call = "pass" if c["db"] >= c["run"] else "run"
                if down == 4 and max(c["punt"], c["fg"]) > c["run"] + c["db"]:
                    call = "punt" if c["punt"] >= c["fg"] else "field goal"
                rows.append({
                    "down": down, "per_game": round(c["n"] / n_games, 1), "dist": round(c["dist"] / c["n"], 1),
                    "run_pct": pct(c["run"], c["n"]), "pass_pct": pct(c["db"], c["n"]),
                    "punt_pct": pct(c["punt"], c["n"]), "fg_pct": pct(c["fg"], c["n"]),
                    "call": call,
                    "run_med": _hmed(c["h_run"]), "pass_med": _hmed(c["h_db"]), "yds_med": _hmed(hall),
                    "yds_mean": round(c["yds"] / c["plays"], 1) if c["plays"] else None,
                    "success": pct(c["succ"], c["plays"]), "first": pct(c["first"], c["plays"]),
                    "cmp_pct": pct(c["cmp"], c["att"]), "sack_pct": pct(c["sack"], c["db"]),
                    "int_pct": pct(c["int"], c["att"])})
            out[name] = rows
        return out


# ---- Flavour from this season's nflverse store --------------------------------------
def _shrunk(counts, n, league):
    return [(lab, (counts.get(lab, 0) + _FLAVOR_K * p) / (n + _FLAVOR_K)) for lab, p in league]


def flavor(season):
    """({team: {"run": [(label, p)], "pass": [(side, p)], "sg_db": p, "sg_run": p}},
    plays read). Each team's mix this season from the nflverse store
    (nfl_matchup_data), shrunk toward the league's 2025 mix by _FLAVOR_K plays;
    ({}, 0) without a store -- every team is then the league."""
    try:
        import nfl_data
        import nfl_matchup_data as D
        st = D.load_season(int(season))
    except Exception as e:
        errlog.note("NFLV-flavor", e)
        return {}, 0
    if not st or not st.get("n"):
        return {}, 0
    c, voc = st["cols"], st.get("vocab") or {}
    side, gap, kinds = voc.get("side") or D.SIDE, voc.get("gap") or D.GAP, voc.get("kind") or D.KIND
    k_run, k_att = kinds.index("run"), kinds.index("attempt")
    teams = [nfl_data._canon(t) for t in st["teams"]]
    acc = {}
    for i in range(st["n"]):
        tm = teams[c["off"][i]]
        a = acc.get(tm)
        if a is None:
            a = acc[tm] = {"run": {}, "nrun": 0, "pass": {}, "npass": 0, "db": 0, "sg_db": 0, "runs": 0, "sg_run": 0}
        kind, sg = c["kind"][i], c["sg"][i] == 1
        if c["db"][i] == 1:
            a["db"] += 1
            a["sg_db"] += sg
        elif kind == k_run:
            a["runs"] += 1
            a["sg_run"] += sg
        if kind == k_run:
            rl, rg = side[c["rloc"][i]], gap[c["rgap"][i]]
            lab = "up the middle" if rl == "middle" else f"{rl} {rg}" if rl in ("left", "right") and rg else None
            if lab:
                a["run"][lab] = a["run"].get(lab, 0) + 1
                a["nrun"] += 1
        elif kind == k_att:
            pl = side[c["ploc"][i]]
            if pl in ("left", "middle", "right"):
                a["pass"][pl] = a["pass"].get(pl, 0) + 1
                a["npass"] += 1
    out = {}
    for tm, a in acc.items():
        out[tm] = {"run": _shrunk(a["run"], a["nrun"], _RUN_MIX), "pass": _shrunk(a["pass"], a["npass"], _PASS_SIDE),
                   "sg_db": (a["sg_db"] + _FLAVOR_K * _SG_DROPBACK) / (a["db"] + _FLAVOR_K),
                   "sg_run": (a["sg_run"] + _FLAVOR_K * _SG_RUN) / (a["runs"] + _FLAVOR_K)}
    return out, int(st["n"])


def _league_flavor():
    return {"run": list(_RUN_MIX), "pass": list(_PASS_SIDE), "sg_db": _SG_DROPBACK, "sg_run": _SG_RUN}


# ---- Names from Sleeper's week -------------------------------------------------------
def sleeper_rows(season, week):
    """(offence, IDP, kicker) rows of Sleeper's weekly projections; a failed
    fetch is an empty list (that side's events are then told without names)."""
    import nfl_data
    return tuple(nfl_data._rows(int(season), int(week), pos) or [] for pos in (_OFF_POS, _IDP_POS, ("K",)))


def _short(first, last):
    f, la = (first or "").strip(), (last or "").strip()
    return f"{f[:1]}.{la}" if f and la else (la or f)


def rosters(rows, teams, doubt=None):
    """(players, roles) for the game's two clubs (`teams`: home, away, nfl_data
    codes). players: [{"n": "J.Allen", "f": "Josh Allen", "p": "QB", "t": side}];
    roles[side]: {role: [(player index, weight)]} plus "qb" and "k" (one index or
    None). Nobody tagged out-type (nfl_game_sim._AVAIL_OUT) is drawn for anything,
    nobody the slate holds in doubt (`doubt`: {(team, full name)} from
    nfl_game_sim.availability -- "Questionable after missing his last game" is
    the rule that takes a quarterback's legs off the board, and the viewer named
    him as the thrower of every pass), and nobody under _MIN_SHARE of his team's
    role total."""
    import nfl_data
    from nfl_game_sim import _AVAIL_OUT
    off_rows, idp_rows, k_rows = rows
    side_of = {t: s for s, t in enumerate(teams)}
    players, index = [], {}
    roles = [{} for _ in teams]

    def player(r, side):
        pid = str(r.get("player_id") or "")
        if pid in index:
            return index[pid]
        p = r.get("player") or {}
        nm = " ".join(x for x in ((p.get("first_name") or "").strip(), (p.get("last_name") or "").strip()) if x)
        players.append({"n": _short(p.get("first_name"), p.get("last_name")), "f": nm,
                        "p": p.get("position") or "", "t": side})
        index[pid] = len(players) - 1
        return index[pid]

    def usable(r):
        team = nfl_data._canon(r.get("team"))
        side = side_of.get(team)
        if side is None:
            return None
        p = r.get("player") or {}
        if (p.get("injury_status") or "") in _AVAIL_OUT:
            return None
        full = " ".join(x for x in ((p.get("first_name") or "").strip(), (p.get("last_name") or "").strip()) if x)
        if doubt and (team, full) in doubt:
            return None
        return side

    cand = [{} for _ in teams]           # side -> role -> [(row, weight)]

    def add(side, role, r, w):
        if w and w > 0:
            cand[side].setdefault(role, []).append((r, float(w)))

    for r in off_rows:
        side = usable(r)
        if side is None:
            continue
        st = r.get("stats") or {}
        pos = (r.get("player") or {}).get("position")
        if pos == "QB":
            add(side, "qb", r, st.get("pass_att"))
        add(side, "car", r, st.get("rush_att"))
        add(side, "rtd", r, st.get("rush_td"))
        if pos != "QB":
            add(side, "rec", r, st.get("rec"))
            add(side, "rctd", r, st.get("rec_td"))
            add(side, "miss", r, (st.get("rec_tgt") or 0) - (st.get("rec") or 0))
        add(side, "kr", r, st.get("def_kr_yd"))
        add(side, "pr", r, st.get("pr_yd"))
    for r in idp_rows:
        side = usable(r)
        if side is None:
            continue
        st = r.get("stats") or {}
        add(side, "tkl", r, st.get("idp_tkl_solo") or st.get("idp_tkl"))
        add(side, "sack", r, st.get("idp_sack"))
        add(side, "int", r, st.get("idp_int"))
        add(side, "ff", r, st.get("idp_ff"))
        add(side, "fr", r, st.get("idp_fum_rec"))
        add(side, "kr", r, st.get("def_kr_yd"))
        add(side, "pr", r, st.get("def_pr_yd"))
    for r in k_rows:
        side = usable(r)
        if side is None:
            continue
        st = r.get("stats") or {}
        add(side, "k", r, (st.get("fga") or 0) + (st.get("xpa") or 0) or st.get("pts_ppr"))
    for side in range(len(teams)):
        for role, lst in cand[side].items():
            tot = sum(w for _r, w in lst)
            if role in ("qb", "k"):
                # one man: the most projected one left, however small his share
                # (a starter held in doubt hands the passes to the backup, whose
                # projection is a token one until Sleeper moves it)
                keep = sorted(lst, key=lambda rw: -rw[1])
                roles[side][role] = player(keep[0][0], side) if keep else None
            else:
                keep = sorted(((r, w) for r, w in lst if w >= _MIN_SHARE * tot), key=lambda rw: -rw[1])
                roles[side][role] = [(player(r, side), w) for r, w in keep]
        roles[side].setdefault("qb", None)
        roles[side].setdefault("k", None)
    return players, roles


def _draw(rng, cands):
    """One index of `cands` [(index, weight)] by weight, or None for an empty list."""
    if not cands:
        return None
    tot = sum(w for _i, w in cands)
    x = rng.random() * tot
    for i, w in cands:
        x -= w
        if x < 0:
            return i
    return cands[-1][0]


def _mix(rng, mix):
    x = rng.random() * sum(p for _l, p in mix)
    for lab, p in mix:
        x -= p
        if x < 0:
            return lab
    return mix[-1][0]


# ---- Telling one game ------------------------------------------------------------------
_ORD = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}
_PERIOD = {1: "the 1st quarter", 2: "the 2nd quarter", 3: "the 3rd quarter", 4: "the 4th quarter"}


def _ax(side, ytg):
    """The absolute yard line of a ball `ytg` yards from `side`'s goal: 0 is the
    home goal line, 100 the away one; home attacks toward 100 all game."""
    return 100 - ytg if side == 0 else ytg


def _spot(teams, side, ytg):
    if ytg == 50:
        return "the 50"
    if ytg > 50:
        return f"the {teams[side]} {100 - ytg}"
    return f"the {teams[1 - side]} {ytg}"


def _yds(n):
    if n == 0:
        return "no gain"
    if n < 0:
        return f"a loss of {-n}"
    return f"{n} yard" + ("" if n == 1 else "s")


def tell(log, players, roles, teams, seed, flav=None):
    """The display events of one logged game: text, the ball's path on the field,
    the named players and their box-score credits ("cr": [[player, stat, n]]).
    Draws only from its own generator (the game's seed xor _NAME_SALT)."""
    rng = random.Random(seed ^ _NAME_SALT)
    flav = flav or {}
    fl = [flav.get(teams[0]) or _league_flavor(), flav.get(teams[1]) or _league_flavor()]
    nm = [p["n"] for p in players]
    out = []
    pending_ret = None                           # the returner / interceptor of a return touchdown

    def name(i, fallback):
        return nm[i] if i is not None else fallback

    for e in log:
        k, o = e["k"], e.get("o")
        ev = {"k": k, "q": e["q"], "c": e["c"], "s": list(e["s"])}
        if o is not None:
            ev["o"] = o
        cr = []
        if k in _PLAYS or k == "kneel":
            d_, t, y = e["d"], e["t"], e["y"]
            off, dfn = roles[o], roles[1 - o]
            ev.update(d=d_, t=t, y=y, x0=_ax(o, y), fx=_ax(o, 0 if t >= y else y - t), yd=e.get("yd", 0))
            qb = off.get("qb")
            qbn = name(qb, f"{teams[o]} QB")
            dropback = k in ("cmp", "inc", "int", "sack")
            ev["f"] = "g" if rng.random() < fl[o]["sg_db" if dropback else "sg_run"] else "u"
            yd = e.get("yd", 0)
            td, to = bool(e.get("td")), e.get("to")
            oob = bool(e.get("ob"))
            txt, ban, ca, df = "", None, None, None
            if k == "kneel":
                ca = qb
                txt = f"{qbn} kneels."
                ev["x1"] = _ax(o, min(99, y + 1))
            elif k == "run":
                ca = _draw(rng, off.get("rtd") if td and off.get("rtd") else off.get("car"))
                dirn = _mix(rng, fl[o]["run"])
                ev["lr"] = -1 if dirn.startswith("left") else 1 if dirn.startswith("right") else 0
                who = name(ca, teams[o])
                cr += [[ca, "ra", 1], [ca, "ry", yd]]
                if td:
                    cr.append([ca, "rtd", 1])
                    txt = f"{who} runs {dirn}, {yd} yard{'s' if yd != 1 else ''} for a TOUCHDOWN."
                else:
                    txt = f"{who} runs {dirn} for {_yds(yd)}"
            elif k in ("cmp", "inc", "int"):
                side_ = _mix(rng, fl[o]["pass"])
                if k == "cmp":
                    deep = rng.random() < next(p for b, p in _DEEP_CMP if yd < b)
                    ca = _draw(rng, off.get("rctd") if td and off.get("rctd") else off.get("rec"))
                else:
                    deep = rng.random() < (_DEEP_INC if k == "inc" else _DEEP_INT)
                    ca = _draw(rng, off.get("miss"))
                ev["lr"] = {"left": -1, "middle": 0, "right": 1}[side_]
                ev["deep"] = int(deep)
                how = f"{'deep' if deep else 'short'} {side_}"
                who = name(ca, "a receiver")
                cr += [[qb, "pa", 1], [ca, "tg", 1]]
                if k == "cmp":
                    cr += [[qb, "pc", 1], [qb, "py", yd], [ca, "rc", 1], [ca, "rcy", yd]]
                    if td:
                        cr += [[qb, "ptd", 1], [ca, "rctd", 1]]
                        txt = f"{qbn} pass {how} to {who}, {yd} yard{'s' if yd != 1 else ''}, TOUCHDOWN."
                    else:
                        txt = f"{qbn} pass {how} to {who} for {_yds(yd)}"
                elif k == "inc":
                    txt = f"{qbn} pass incomplete {how}, intended for {who}."
                    ev["x1"] = ev["x0"]
                else:
                    df = _draw(rng, dfn.get("int"))
                    cr += [[qb, "pi", 1], [df, "in", 1]]
                    by = name(df, teams[1 - o])
                    # The engine draws where the defence takes over, net of the
                    # throw and the return together (nfl_deep_fit's int_ret) --
                    # never a return length, so none is told.
                    if e.get("six"):
                        cr.append([df, "dtd", 1])
                        pending_ret = df
                        txt = (f"{qbn} pass {how}, intended for {who}, INTERCEPTED by {by} and returned "
                               "for a TOUCHDOWN.")
                        ev["x1"] = _ax(1 - o, 0)
                    else:
                        txt = (f"{qbn} pass {how}, intended for {who}, INTERCEPTED by {by}. {teams[1 - o]} "
                               f"takes over at {_spot(teams, 1 - o, e['ty'])}.")
                        ev["x1"] = _ax(1 - o, e["ty"])
                    ban = "int"
            else:                                  # sack
                ca = qb
                df = _draw(rng, dfn.get("sack"))
                cr += [[qb, "sk", 1], [qb, "sky", -yd], [df, "sa", 1]]
                if yd > 0:
                    # The engine's sack yards are EFFECTIVE yards (where the next
                    # snap was, penalties enforced: nfl_deep_fit), and about one
                    # sack in twelve it draws nets yards -- most of them a fit
                    # error on fourth-down turnovers (research/reports/nfl_viewer.md
                    # section 6.2). Told as the engine played it, and MARKED: it
                    # sat unmarked in the typical game of the first matchup the
                    # owner would open (Fable's build review, M1).
                    txt = (f"{qbn} sacked by {name(df, teams[1 - o])}; the play nets +{_yds(yd)} "
                           "(a fitted-table artifact: see Not modelled)")
                    ev["art"] = 1
                else:
                    txt = f"{qbn} sacked by {name(df, teams[1 - o])} for {_yds(yd)}"
            if k in ("run", "cmp", "sack") and not td:
                if e.get("sf"):
                    if k != "sack":
                        df = _draw(rng, dfn.get("tkl"))
                        cr.append([df, "tk", 1])
                        txt += f", tackled in the end zone by {name(df, teams[1 - o])}"
                    txt += ": SAFETY."
                    ban = "sf"
                    ev["x1"] = _ax(o, 100)
                elif to == "fum":
                    ff = df if k == "sack" else _draw(rng, dfn.get("ff"))
                    fr = _draw(rng, dfn.get("fr"))
                    cr += [[ca, "fl", 1], [ff, "ff", 1], [fr, "fr", 1]]
                    ev["xi"] = _ax(o, y - yd)
                    ev["x1"] = _ax(1 - o, e["ty"])
                    df = fr if fr is not None else ff
                    txt += (f" -- FUMBLES, forced by {name(ff, teams[1 - o])}, recovered by "
                            f"{name(fr, teams[1 - o])} at {_spot(teams, 1 - o, e['ty'])}.")
                    ban = "fum"
                else:
                    if k != "sack":
                        if oob:
                            txt += ", out of bounds"
                        else:
                            df = _draw(rng, dfn.get("tkl"))
                            cr.append([df, "tk", 1])
                            txt += f" (tackled by {name(df, teams[1 - o])})"
                    if to == "downs":
                        txt += f". TURNOVER ON DOWNS at {_spot(teams, 1 - o, e['ty'])}."
                        ev["x1"] = _ax(1 - o, e["ty"])
                        ban = "tod"
                    else:
                        y2 = e.get("y2", y - yd)
                        ev["x1"] = _ax(o, y2)
                        if e.get("fd"):
                            txt += ". First down."
                        else:
                            txt += "."
                        if y - yd >= 100:
                            txt += f" Ball at {_spot(teams, o, 99)}."
            if td:
                ev["x1"] = _ax(o, 0)
                ban = "td"
            if k == "inc" and to == "downs":
                txt += f" TURNOVER ON DOWNS at {_spot(teams, 1 - o, e['ty'])}."
                ban = "tod"
            ev["txt"] = txt
            if ca is not None:
                ev["ca"] = ca
            if df is not None:
                ev["df"] = df
            if ban:
                ev["b"] = ban
            if e.get("fd") or td:
                ev["fd"] = 1
        elif k == "pen":
            o_ = o
            ev.update(d=e["d"], t=e["t"], y=e["y"], x0=_ax(o_, e["y"]), x1=_ax(o_, e["y2"]),
                      fx=_ax(o_, 0 if e["t"] >= e["y"] else e["y"] - e["t"]), yd=e["yd"])
            yd = e["yd"]
            txt = f"Penalty, no play: {'+' if yd > 0 else ''}{yd} yards"
            # `fd`: the next snap was a first down (the fit's flag) -- by the
            # penalty's yards or automatically; the engine does not say which
            ev["txt"] = txt + (". First down." if e.get("fd") else ".")
        elif k == "punt":
            ev.update(d=e["d"], t=e["t"], y=e["y"], x0=_ax(o, e["y"]))
            res = e["res"]
            if res == "td":
                ret = _draw(rng, roles[1 - o].get("pr"))
                pending_ret = ret
                cr.append([ret, "ktd", 1])
                ev["x1"] = _ax(1 - o, 0)
                ev["txt"] = f"{teams[o]} punts -- returned for a TOUCHDOWN by {name(ret, teams[1 - o])}!"
                if ret is not None:
                    ev["ca"] = ret
            else:
                ev["x1"] = _ax(1 - o, e["y2"])
                if res == "blocked":
                    ev["txt"] = f"{teams[o]} punt BLOCKED. {teams[1 - o]} takes over at {_spot(teams, 1 - o, e['y2'])}."
                    ev["b"] = "blk"
                else:
                    ev["txt"] = f"{teams[o]} punts. {teams[1 - o]} takes over at {_spot(teams, 1 - o, e['y2'])}."
        elif k == "fg":
            kk = roles[o].get("k")
            kn = name(kk, teams[o])
            ev.update(d=e["d"], t=e["t"], y=e["y"], x0=_ax(o, e["y"]), kd=e["kd"])
            pre = "With the clock running out, " if e.get("x") else ""
            cr.append([kk, "fga", 1])
            if e["res"] == "good":
                cr.append([kk, "fgm", 1])
                ev["x1"] = _ax(o, -10)
                ev["txt"] = f"{pre}{kn} {e['kd']}-yard field goal is GOOD."
                ev["b"] = "fg"
            else:
                ev["x1"] = _ax(1 - o, e["y2"])
                if e["res"] == "blocked":
                    ev["txt"] = (f"{pre}{kn} {e['kd']}-yard field goal is BLOCKED. {teams[1 - o]} takes over at "
                                 f"{_spot(teams, 1 - o, e['y2'])}.")
                    ev["b"] = "blk"
                else:
                    ev["txt"] = (f"{pre}{kn} {e['kd']}-yard field goal is NO GOOD. {teams[1 - o]} takes over at "
                                 f"{_spot(teams, 1 - o, e['y2'])}.")
                    ev["b"] = "fgx"
            if kk is not None:
                ev["ca"] = kk
        elif k == "try":
            kk = roles[o].get("k")
            if e["two"]:
                ev.update(x0=_ax(o, 2), x1=_ax(o, 0) if e["ok"] else _ax(o, 2))
                ev["txt"] = f"{teams[o]} two-point try {'is GOOD' if e['ok'] else 'fails'}."
            else:
                cr.append([kk, "xpa", 1])
                if e["ok"]:
                    cr.append([kk, "xpm", 1])
                ev.update(x0=_ax(o, 15), x1=_ax(o, -10) if e["ok"] else _ax(o, 15))
                ev["txt"] = f"{name(kk, teams[o])} extra point is {'good' if e['ok'] else 'NO GOOD'}."
                if kk is not None:
                    ev["ca"] = kk
        elif k == "ko":
            ev["x0"] = _ax(o, 65)
            res = e["res"]
            if res == "td":
                ret = _draw(rng, roles[1 - o].get("kr"))
                pending_ret = ret
                cr.append([ret, "ktd", 1])
                ev["x1"] = _ax(1 - o, 0)
                ev["txt"] = f"{teams[o]} kicks off -- returned for a TOUCHDOWN by {name(ret, teams[1 - o])}!"
                if ret is not None:
                    ev["ca"] = ret
            elif res == "onside_kept":
                ev["x1"] = _ax(o, e["y2"])
                ev["txt"] = f"{teams[o]} onside kick -- RECOVERED by {teams[o]} at {_spot(teams, o, e['y2'])}."
                ev["b"] = "onside"
            elif res == "onside_lost":
                ev["x1"] = _ax(1 - o, e["y2"])
                ev["txt"] = f"{teams[o]} onside kick -- {teams[1 - o]} recovers at {_spot(teams, 1 - o, e['y2'])}."
            else:
                ev["x1"] = _ax(1 - o, e["y2"])
                ev["txt"] = f"{teams[o]} kicks off. {teams[1 - o]} starts at {_spot(teams, 1 - o, e['y2'])}."
        elif k == "fk":
            ev.update(x0=_ax(o, 80), x1=_ax(1 - o, e["y2"]))
            ev["txt"] = f"{teams[o]} free kick after the safety. {teams[1 - o]} starts at {_spot(teams, 1 - o, e['y2'])}."
        elif k == "rtd":
            ev["txt"] = f"TOUCHDOWN, {teams[o]}" + (f" ({nm[pending_ret]})." if pending_ret is not None else ".")
            ev["b"] = "td"
            pending_ret = None
        elif k == "2mw":
            ev["txt"] = "Two-minute warning."
            ev["b"] = "2mw"
        elif k == "end":
            q = e["q"]
            h_, a_ = e["s"]
            if q == 2:
                ev["txt"] = "Halftime."
            elif q == 4:
                ev["txt"] = "End of regulation." + (" Tied: overtime." if h_ == a_ else "")
            elif q >= 5:
                ev["txt"] = "End of overtime."
            else:
                ev["txt"] = f"End of {_PERIOD[q]}."
            ev["b"] = "end"
        elif k == "ot":
            kick = e["kick"]
            ev["o"] = kick
            ev["txt"] = f"Overtime: {teams[kick]} kicks off to {teams[1 - kick]}."
            ev["b"] = "ot"
        elif k == "fin":
            h_, a_ = e["s"]
            ev["txt"] = (f"FINAL{' (OT)' if e.get('ot') else ''}: {teams[1]} {a_}, {teams[0]} {h_}"
                         + (" -- a tie." if h_ == a_ else "."))
            ev["b"] = "fin"
        else:
            ev["txt"] = ""
        cr = [c for c in cr if c[0] is not None]
        if cr:
            ev["cr"] = cr
        out.append(ev)
    return out


def team_box(b):
    """The engine's box for one side, the fields the viewer prints."""
    keep = ("pts", "q", "plays", "pass_att", "comp", "pass_yds", "sacks", "sack_yds", "int", "rush_att",
            "rush_yds", "td_pass", "td_rush", "td_ret", "fg_made", "fg_att", "xp_made", "two_made", "punts",
            "fum_lost", "first", "pen", "top", "third_att", "third_conv", "fourth_go", "fourth_conv",
            "rz_trips", "rz_td", "kneels")
    return {k: b[k] for k in keep}


# ---- One matchup ----------------------------------------------------------------------
def _sha(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:12]
    except OSError:
        return None


def simulate_matchup(home_prof, away_prof, season, week, T=None, n=None, k_random=None):
    """The selection run: n games on their own seeds, every log folded into the
    per-down table and dropped. Returns (sums, downs, typical, others, choice):
    typical / others are (index, log, result) replayed with the log on; a
    replay whose coordinates differ from the selection run's raises."""
    import nfl_deep_sim as E
    T = T or E.tables()
    n = N_SIMS if n is None else int(n)
    k_random = K_RANDOM if k_random is None else int(k_random)
    h, a = home_prof["abbr"], away_prof["abbr"]
    sums, downs = [], _Downs()
    for i in range(n):
        lg = []
        g = E.play_game(home_prof, away_prof, random.Random(game_seed(season, week, h, a, i)), T, log=lg)
        sums.append(summary(g))
        downs.add(lg)
    best, centre, scale, side, dist = choose(sums)
    if best is None:
        raise RuntimeError(f"no candidate game for {a}@{h}")

    def replay(i):
        lg = []
        g = E.play_game(home_prof, away_prof, random.Random(game_seed(season, week, h, a, i)), T, log=lg)
        if summary(g) != sums[i]:
            raise RuntimeError(f"{a}@{h} game {i} did not replay: {summary(g)} vs {sums[i]}")
        return i, lg, g
    picks = [i for i in range(k_random + 1) if i != best][:k_random]
    return sums, downs, replay(best), [replay(i) for i in picks], (best, centre, scale, side, dist)


def build_game(season, week, home, away, kickoff_ts=None, home_name=None, away_name=None, by="server",
               rows=None, flav=None, profs=None, T=None, n=None, doubt=None):
    """One game's piece (not published). `rows` (sleeper_rows), `flav` (flavor),
    `profs` and `doubt` (rosters) are passed in by a week's build so they are
    fetched once; `n` (default N_SIMS) is for tests."""
    import nfl_deep_sim as E
    import nfl_game_sim
    T = T or E.tables()
    if profs is None:
        profs = nfl_game_sim._deep_profiles(int(season), int(week))
    if not profs or home not in profs or away not in profs:
        raise RuntimeError(f"no deep profiles for {away}@{home}")
    pc = nfl_game_sim._deep_prof_cache
    src = {"src": pc.get("src"), "src_ts": pc.get("src_ts"), "t": pc.get("t"), "factors": pc.get("factors")}
    if flav is None:
        flav = flavor(season)
    flav_map, flav_n = flav
    if rows is None:
        rows = sleeper_rows(season, week)
    hp, ap = profs[home], profs[away]
    t0 = time.time()
    sums, downs, typ, others, (best, centre, scale, side, dist) = simulate_matchup(hp, ap, season, week, T, n=n)
    sim_s = time.time() - t0
    board = E.simulate(hp, ap, n=nfl_game_sim._DEEP_N,
                       seed=zlib.crc32(f"{int(season)}|{int(week)}|{home}|{away}".encode()))
    teams = (home, away)
    players, roles = rosters(rows, teams, doubt)
    games, sims = [], []
    for kind, (i, lg, g) in [("typical", typ)] + [("random", o) for o in others]:
        seed = game_seed(season, week, home, away, i)
        s = sums[i]
        z = [round((x - c) / sc, 2) for x, c, sc in zip(s, centre, scale)]
        sims.append({"kind": kind, "i": i, "seed": seed, "summary": list(s), "z": z,
                     "dist": round(sum(v * v for v in ((x - c) / sc for x, c, sc in zip(s, centre, scale))), 3)})
        games.append(json.dumps({"events": tell(lg, players, roles, teams, seed, flav_map),
                                 "box": {"home": team_box(g["h"]), "away": team_box(g["a"])},
                                 "ot": g["ot"], "tie": g["tie"]}, separators=(",", ":")))
    empty = [(teams[s_], r) for s_ in (0, 1) for r in _ROLE_WORDS if roles[s_].get(r) is None or roles[s_].get(r) == []]
    missing = [f"{t}: no {_ROLE_WORDS[r]}" for t, r in empty]
    core = [f"{t} {r}" for t, r in empty if r in ("qb", "car", "rec", "tkl", "k")]
    if core:
        # a Sleeper key renamed or a week with no IDP lines: the plays are told
        # without names, and the only other signal would be the footnote
        errlog.note("NFLV-roles", msg=f"{season} w{week} {away}@{home}: no {', '.join(core)}")
    return {
        "schema": SCHEMA, "season": int(season), "week": int(week), "home": home, "away": away,
        "home_name": home_name or home, "away_name": away_name or away, "kickoff_ts": kickoff_ts,
        "built_ts": time.time(), "by": by, "n_sims": len(sums), "sim_s": round(sim_s, 1),
        "dims": list(DIMS), "centre": centre, "scale": [round(x, 3) for x in scale],
        "side": side, "pickem": side == 0,
        "board": {"p_home": board["p_home"], "p_away": board["p_away"], "p_tie": board["p_tie"],
                  "exp_home": board["exp_home"], "exp_away": board["exp_away"], "n": board["n_sims"]},
        "per_down": downs.table(len(sums)),
        "players": players, "sims": sims, "games": games,
        "names": {"source": f"Sleeper's week {int(week)} projections", "missing": missing},
        "flavor": {"source": (f"nflverse {int(season)}, {flav_n:,} plays, each team shrunk toward the league"
                              if flav_n else "the league's 2025 mix (no store for this season)")},
        "provenance": {
            "profiles": src.get("src"), "profiles_ts": src.get("src_ts"), "profiles_built_ts": src.get("t"),
            "roster_factors": {t: (src.get("factors") or {}).get(t) for t in teams},
            "tables": os.path.basename(E.TABLES_PATH), "tables_sha": _sha(E.TABLES_PATH),
            "kickoff_era": getattr(T, "kickoff_era", None),
            "engine_sha": _sha(os.path.abspath(E.__file__)),
            "seed_scheme": "crc32('season|week|home|away|i'), random.Random per game",
            "board_seed_scheme": "nfl_game_sim.board: crc32('season|week|home|away'), n=_DEEP_N"},
    }


def week_schedule(season, week):
    """[(home, away, kickoff_ts, state, home_name, away_name)] of the week, in
    nfl_data codes (the deep profiles' and Sleeper's), off ESPN's scoreboard
    alone (cached an hour) -- the route reads it, and the board's pairing
    (nfl_data.week_games) would parse Sleeper's whole week in a web worker."""
    import nfl_data
    import nfl_live
    import predlog
    out = []
    for g in nfl_live.schedule(int(week), int(season)) or []:
        h, a = nfl_data._canon(g.get("home")), nfl_data._canon(g.get("away"))
        if h and a:
            out.append((h, a, predlog.iso_ts(g.get("date")), (g.get("state") or "").lower(),
                        g.get("home_name") or h, g.get("away_name") or a))
    return out


def upcoming(g, now=None):
    """A game the viewer may still build: kickoff known, at least ten minutes
    away, and ESPN does not have it under way."""
    now = time.time() if now is None else now
    return bool(g[2]) and g[2] - now > 600 and g[3] not in ("in", "post")


def build_week(season, week, games, by="server", budget_s=None, log=None):
    """Build and publish the pieces for `games` (week_schedule rows), the shared
    inputs fetched once; stops starting new games past `budget_s`. Returns
    {"built": [...], "failed": [...], "left": [...]}."""
    import boardshare
    import nfl_deep_sim as E
    import nfl_game_sim
    t0 = time.time()
    res = {"built": [], "failed": [], "left": []}
    todo = []
    for g in games:
        if upcoming(g):
            todo.append(g)
        else:
            res["failed"].append(f"{g[1]}@{g[0]}: started")
    if not todo:
        return res
    T = E.tables()
    profs = nfl_game_sim._deep_profiles(int(season), int(week))
    flav = flavor(season)
    rows = sleeper_rows(season, week)
    try:
        doubt = {(t, f.get("name")) for t, fl in (nfl_game_sim.availability(int(season), int(week)) or {}).items()
                 for f in fl}
    except Exception as e:
        errlog.note("NFLV-doubt", e)
        doubt = set()
    for g in todo:
        home, away, ko, state, hn, an = g
        tag = f"{away}@{home}"
        if budget_s is not None and time.time() - t0 > budget_s:
            res["left"].append(tag)
            continue
        try:
            piece = build_game(season, week, home, away, ko, hn, an, by=by, rows=rows, flav=flav, profs=profs, T=T,
                               doubt=doubt)
            if not upcoming(g):              # the build ran past the cutoff: a pre-game piece only
                res["failed"].append(f"{tag}: kicked off during the build")
                continue
            size = len(pickle.dumps(piece, protocol=pickle.HIGHEST_PROTOCOL))
            if size > PIECE_MAX:
                errlog.note("NFLV-size", msg=f"{tag}: {size:,} bytes over PIECE_MAX {PIECE_MAX:,}")
                res["failed"].append(f"{tag}: {size:,} bytes, over PIECE_MAX")
                continue
            boardshare.put(piece_name(season, week, home, away), piece)
            res["built"].append(tag)
            if log:
                log(f"[nfl-viewer] {tag}: typical game {piece['sims'][0]['summary'][:2]} in {piece['sim_s']} s")
        except Exception as e:
            errlog.note("NFLV-build", e, path=tag)
            res["failed"].append(f"{tag}: {type(e).__name__}")
    return res


def build_isolated(season, week, games, by="server", budget_s=None, timeout=None):
    """build_week in a niced child through deep_cache.run_child -- the result
    pickle on a private copy of fd 1 (a stray print anywhere in the build cannot
    corrupt it) and the child's stderr tail in the error (a bare "rc=1" told the
    ledger nothing; Fable m3). The parent receives only the summary; a failed
    child returns None, ledgered NFLV-child with its reason."""
    import deep_cache
    timeout = timeout or (_GAME_TIMEOUT_S * max(1, len(games)) if budget_s is None else budget_s + _GAME_TIMEOUT_S)
    try:
        return deep_cache.run_child("nfl_viewer", "build_week", int(season), int(week), list(games),
                                    by=by, budget_s=budget_s, timeout=timeout)
    except Exception as e:
        errlog.note("NFLV-child", e, path=f"{season} w{week}")
    return None


def _stale(mt, ko, now, max_age=REBUILD_S, late_window=_LATE_WINDOW_S):
    """Is a piece published at `mt` due again: missing, older than `max_age`, or
    built before Sunday's inactives (kickoff - _INACTIVES_S) with kickoff now
    inside `late_window`."""
    if mt is None or now - mt > max_age:
        return True
    return bool(ko) and mt < ko - _INACTIVES_S and ko - now <= late_window


def _due(season, week, games, now=None):
    """The week's upcoming games whose piece is missing or stale (_stale)."""
    import boardshare
    now = time.time() if now is None else now
    have = dict(boardshare.names(PREFIX))
    return [g for g in games
            if upcoming(g, now) and _stale(have.get(piece_name(season, week, g[0], g[1])), g[2], now)]


# The PC's boards pass gives the viewer this long a pass (no new game starts past
# it): a week's 14-16 games at 9.6 s each (8.8-11.6) take one or two passes, the
# rest going to the next pass five minutes later. Every piece comes due twice a
# day (REBUILD_S), and once more after Sunday's inactives, so the other boards'
# first upload waits up to this long two or three times a day -- inside their
# 12-minute TTL slack (boardshare.PC_SLACK_S). Most passes have nothing due.
_PC_BUDGET_S = 150


def pc_build():
    """The PC's boards pass (pc_worker._task_boards): every upcoming game of the
    current week whose piece is missing or due, built in a niced child, published
    to the PC's boards store for the boards sync. Never None (the pass reads None
    as "still building")."""
    import nfl_game_sim
    import nfl_preseason
    if nfl_preseason.is_preseason():
        return {"skipped": "preseason: the deep engine plays regular-season games only"}
    season = nfl_game_sim._season()
    week = nfl_game_sim.current_week()
    games = week_schedule(season, week)
    due = _due(season, week, games)
    if not due:
        return {"skipped": f"week {week}: every upcoming game's viewer is fresh"}
    res = build_isolated(season, week, due, by="PC", budget_s=_PC_BUDGET_S)
    if res is None:
        return {"skipped": "the viewer build failed (ledgered NFLV-child)"}
    return {"built": res["built"], "failed": res["failed"], "left": res["left"]}


# ---- Serving (the route; reads only, never simulates) -----------------------------------
_MEM = {}               # this worker's server builds in flight: {name: start ts}
_BUILD_RETRY_S = 300


def _gate_held():
    """Is a server viewer build running in some worker? A read of the
    _SERVER_GATE claim's lock file, never a claim."""
    import boardshare
    try:
        return time.time() - os.stat(boardshare._path(_SERVER_GATE, "lock")).st_mtime <= boardshare.CLAIM_TIMEOUT
    except OSError:
        return False


def _server_build(season, week, g):
    """One game's build on the server, in a background thread that only waits
    on a niced child under HEAVY_BUILD. One at a time across every worker: the
    _SERVER_GATE claim is taken INSIDE the HEAVY_BUILD block and held for the
    child's life. Taken before the wait, a wait of more than a minute behind
    another heavy build plus the 240 s child outlived CLAIM_TIMEOUT (300 s), and a
    second worker could take the claim over and start a second child (Fable,
    round 2). Returns True when this game's build is running or was just started
    here, False when the gate is held (the caller says a simulation is running)."""
    import threading
    import boardshare
    name = piece_name(season, week, g[0], g[1])
    t = _MEM.get(name)
    if t and time.time() - t < _BUILD_RETRY_S:
        return True
    if _gate_held():
        return False
    asked = _MEM[name] = time.time()

    def _bg():
        try:
            import deep_cache
            with deep_cache.HEAVY_BUILD:
                if not boardshare.claim(_SERVER_GATE):
                    _MEM.pop(name, None)        # another worker's child got there first: the next poll says so
                    return
                try:
                    if (boardshare.mtime(name) or 0) < asked:     # not built elsewhere while this one waited
                        build_isolated(season, week, [g], by="server")
                finally:
                    boardshare.release(_SERVER_GATE)
        except Exception as e:
            errlog.note("NFLV-server", e, path=name)
    threading.Thread(target=_bg, daemon=True).start()
    return True


def _serve_ages():
    """(max_age, late_window) past which the server rebuilds a piece itself.
    While the PC is on, its own rebuild lands up to two passes and the upload's
    slack after REBUILD_S (a 16-game week takes two passes), so the server waits
    that much longer and lets the PC take the inactives window first (Fable m5)."""
    import boardshare
    if boardshare.pc_status().get("state") in ("on", "behind"):
        lag = 2 * _PC_BUDGET_S + boardshare.PC_SLACK_S
        return REBUILD_S + lag, _LATE_WINDOW_S - lag
    return REBUILD_S, _LATE_WINDOW_S


def serve(week, home, away, k=0, now=None):
    """(JSON text, HTTP status) for /api/nfl/viewer: the piece's head and ONE of
    its games (k: 0 the typical game, 1..K_RANDOM the others). A missing piece
    is built here (in a child) while the game is upcoming; a stale one is served
    while its rebuild runs; after kickoff the piece is served marked as the
    pre-game simulation until SHOW_AFTER_KO_S, then refused."""
    import boardshare
    import nfl_game_sim
    import nfl_season
    now = time.time() if now is None else now
    home, away = str(home or "").upper(), str(away or "").upper()
    if home not in nfl_season._DIV_OF or away not in nfl_season._DIV_OF or home == away:
        return json.dumps({"error": "unknown game"}), 400
    try:
        week, k = int(week), int(k)
    except (TypeError, ValueError):
        return json.dumps({"error": "bad week"}), 400
    if not 1 <= week <= 18 or not 0 <= k <= K_RANDOM:
        return json.dumps({"error": "bad week"}), 400
    season = nfl_game_sim._season()
    name = piece_name(season, week, home, away)
    piece, age = boardshare.get(name, None)
    if piece is not None and (not isinstance(piece, dict) or piece.get("schema") != SCHEMA):
        piece = None
    sched, started, too_far = None, True, False
    max_age, late = _serve_ages()
    # the file's own time, not now - age: `now` may be a caller's clock (the guards')
    mt = (boardshare.mtime(name) or (time.time() - age)) if piece is not None else None
    if piece is None or _stale(mt, piece.get("kickoff_ts"), now, max_age, late):
        sched = next((g for g in week_schedule(season, week) if g[0] == home and g[1] == away), None)
        # this week's games and next week's only: a tap on a far week's card
        # would build it on Sleeper rows that are not that week's yet (Fable n7)
        too_far = week > nfl_game_sim.current_week() + _WEEKS_AHEAD
        if sched is not None and upcoming(sched, now) and not too_far:
            started = _server_build(season, week, sched)
    if piece is None:
        if sched is None:
            return json.dumps({"error": "This game is not on the week's schedule."}), 404
        if not upcoming(sched, now):
            return json.dumps({"error": "This game has kicked off; its pre-game simulation was never built."}), 410
        if too_far:
            return json.dumps({"error": "Only this week's and next week's games are simulated."}), 404
        note = (f"Simulating {N_SIMS:,} games of this matchup -- about a minute. This page checks again on its own."
                if started else "A simulation is running; this game follows. This page checks again on its own.")
        return json.dumps({"building": True, "note": note}), 202
    ko = piece.get("kickoff_ts")
    if ko and now > ko + SHOW_AFTER_KO_S:
        return json.dumps({"error": "This game is over: its pre-game simulation is closed."}), 410
    k = min(k, len(piece["games"]) - 1)
    head = {key: piece[key] for key in piece if key != "games"}
    head["age_s"] = round(age)
    head["started"] = bool(ko and now >= ko)
    head["k"] = k
    return '{"head":' + json.dumps(head, separators=(",", ":")) + ',"game":' + piece["games"][k] + "}", 200
