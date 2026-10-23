"""As-of profiles for the NFL matchup engine: what a defense, an offense, a
quarterback and a skill player had SHOWN before a given week, from the
compact nflverse stores (nfl_matchup_data), with nothing from that week or
later in them.

Every profile is built for a cutoff (season, week) and reads only games of
that season with week < cutoff plus whole prior seasons, each prior season
weighted by DECAY ** (seasons back) and no more than BACK seasons back (the
weights are stamped on the profile). The tallies record every (season, week)
they contain, so tests/guards_nfl_matchup_data.py can prove the cutoff holds
rather than trust it.

Every rate is a dict {"v": raw, "n": denominator, "eb": shrunk, "prior",
"k"}: the empirical-Bayes value is (sum + k x prior) / (n + k), the prior the
league figure at the same cutoff (or the player's own overall figure for his
splits), k the number of denominator units at which the measured
split-half reliability of that kind of signal is one half
(research/reports/nfl_matchup_data.md; research/data/nfl_matchup/reliability.json
when the measurement has run, the table below until then). A reader who wants
to shrink differently has the raw sum and the denominator beside it.

What the data really supports, and what it does not:
- Coverage, man/zone, routes, pressure, time to throw and personnel come from
  NGS's POST-snap charting in the participation file (2016-2025; the stores
  built here hold 2018-2025). Coverage is charted on 99.8% of pass attempts,
  on sacks and scrambles only from 2023 and never on runs, so every coverage
  share here is per CHARTED dropback, never per play. The `route` is the
  targeted receiver's on targeted attempts (non-blank on 98.6-99.9% of them;
  nflreadr calls it the "primary receiver's", and on untargeted attempts it is
  blank in 2021-22) -- profiles read it on targets only. There is no 2026
  participation file and nflreadr says the season's file is published after
  the postseason: a 2026 cutoff carries the 2025 tendencies at the
  prior-season weight, every coverage figure for 2026 then reads "n" from
  2025 only, and the FTN columns (blitzers, pass rushers, box, motion, play
  action, screen, catchable, contested) are the only in-season 2026 charting. The charted man share also moves year to year (2024 read
  49% man against 30-36% in the other seasons) -- compare teams within a
  season, or against the season's league mean, never a raw share across
  seasons.
- Alignment (slot vs wide, X/Z, field/boundary) is in NO free source. The
  player profile carries `alignment_proxy`: the share of his targets thrown to
  the middle of the field plus his route mix, labelled kind "proxy" and never
  called alignment.
- Individual cornerback assignments do not exist in these files; "vs this
  corner" cannot be built.
- "Targets per route run" is approximated by targets per dropback the player
  was on the field for (`tgt_per_db`, labelled proxy: a snap on the field is
  not a route).
Fantasy scoring (the owner's convention, FORMATS): reception 1 / 0.5 / 0 for
PPR / half / standard, 0.1 a receiving or rushing yard, 6 a touchdown of any
kind, 0.04 a passing yard, 4 a passing touchdown, -2 an interception, -2 a
fumble lost; no two-point conversions, no yardage bonuses.
"""
import json
import os

import nfl_matchup_data as D

DECAY = 0.5          # a season back counts half (two back a quarter); see the report's persistence table
BACK = 2             # seasons back a profile may reach
RECENT = 4           # "recent" = the last four games
POS = ("QB", "RB", "WR", "TE")
FORMATS = {"std": 0.0, "half": 0.5, "ppr": 1.0}
RELIABILITY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "research", "data", "nfl_matchup", "reliability.json")
_k_path = RELIABILITY_PATH

# k (denominator units at which a signal is half reliable) for each metric kind,
# by denominator: targets for a per-target rate, dropbacks for a per-dropback
# rate, carries, games. These defaults are overwritten by the measured table
# when it exists; `k_source` on every profile says which was used.
# Measured 2026-10-23 on 2018-2025 (research/data/nfl_matchup/reliability.json,
# odd vs even weeks, 256 team-seasons / 1,446 player-seasons / 278 QB-seasons):
# a defense's coverage shares are believed at ~50 charted dropbacks (man r 0.87
# per half), its personnel at ~50 snaps, its blitz rate at ~90 dropbacks; what
# it ALLOWS per target needs ~870 targets (r 0.15 per half -- a season is not
# enough), its position fantasy allowed ~36 games; a player's catch rate / YPT
# / EPA per target ~100 targets, his target rate per on-field dropback ~90.
_K_DEFAULT = {
    "def_cov_share": 52.7, "def_blitz": 90.7, "def_pressure": 437.0, "def_box": 165.3, "def_pers": 48.1,
    "def_allow_target": 865.5, "def_allow_game": 36.1, "def_run": 422.5,
    "def_sack": 621.1, "def_ttt": 535.1, "def_epa_db": 1039.8, "def_scramble": 10849.2,
    "off_rate": 196.9, "qb_rate": 261.6, "qb_depth": 460.5, "qb_ttt": 74.2, "qb_deep": 267.2,
    "pl_target": 100.7, "pl_snap": 90.8, "pl_game": 2.5, "pl_share": 41.0, "pl_carry": 171.0,
    "pl_adot": 4.9, "pl_yac": 17.8,
}
_K = None


def reload_k(path=None):
    """Forget the loaded k table; read `path` (default: the shipped
    reliability.json) on the next k_for. Tests point it at a fixture."""
    global _K, _k_path
    _K = None
    _k_path = path or RELIABILITY_PATH


def k_for(metric):
    """The shrinkage k for a metric kind and where it came from. A missing or
    unreadable table falls back to the code defaults (the same measured
    numbers) and is ledgered, so `k_source` "default" on a profile has a
    reason in the ledger."""
    global _K
    if _K is None:
        _K = {"source": "default", "k": dict(_K_DEFAULT)}
        try:
            with open(_k_path, encoding="utf-8") as fh:
                meas = json.load(fh)
            for m, v in (meas.get("k") or {}).items():
                if m in _K["k"] and v and v > 0:
                    _K["k"][m] = float(v)
            _K["source"] = meas.get("source") or "reliability.json"
        except (OSError, ValueError) as e:
            import errlog
            errlog.note("NFLM-k-table", e, path=_k_path)
    return _K["k"].get(metric, 100.0), _K["source"]


def fantasy_points(line, fmt):
    """Points for a stat line under the convention above. `line` keys: rec,
    rec_yds, rec_td, rush_yds, rush_td, pass_yds, pass_td, intc, fuml."""
    g = lambda k: float(line.get(k) or 0.0)
    return (FORMATS[fmt] * g("rec") + 0.1 * g("rec_yds") + 6.0 * g("rec_td") + 0.1 * g("rush_yds")
            + 6.0 * g("rush_td") + 0.04 * g("pass_yds") + 4.0 * g("pass_td") - 2.0 * g("intc") - 2.0 * g("fuml"))


def rate(num, den, prior=None, k=None, metric=None):
    """{"v", "n", "eb", "prior", "k"} for num / den. With no prior (or no
    denominator) eb is the prior itself when one exists, else None."""
    v = (num / den) if den else None
    if k is None and metric:
        k, _src = k_for(metric)
    if prior is None or k is None:
        eb = v
    else:
        eb = (num + k * prior) / (den + k) if (den or k) else prior
    return {"v": None if v is None else round(v, 4), "n": round(den, 2) if isinstance(den, float) else den,
            "eb": None if eb is None else round(eb, 4),
            "prior": None if prior is None else round(prior, 4), "k": k}


def _add(d, key, vals):
    cur = d.get(key)
    if cur is None:
        d[key] = list(vals)
    else:
        for i, v in enumerate(vals):
            cur[i] += v


def _pos(positions, pid):
    p = positions.get(pid) if positions else None
    return "RB" if p == "FB" else (p if p in POS else "UNK")


def box_bucket(box):
    """Defenders in the box -> 'light' (6 or fewer), 'neutral' (7), 'heavy' (8+),
    None when uncharted."""
    if box is None or box < 0:
        return None
    return "light" if box <= 6 else "neutral" if box == 7 else "heavy"


# Per-game player line layout (one list per player per game)
(G_TGT, G_REC, G_RECY, G_RECTD, G_AIR, G_CAR, G_RUSHY, G_RUSHTD, G_PATT, G_PCMP, G_PASSY, G_PASSTD,
 G_INT, G_FUML, G_RZT, G_RZC, G_STD, G_HALF, G_PPR, G_SNAPS, G_DB, G_RUSHSN, G_MAN, G_ZONE, G_COV0) = range(25)
NC = len(D.COVERAGE) - 1
# the player's on-field dropbacks under each coverage code, then his targets on
# those very plays (all, vs man, vs zone, under each code) -- the numerators of
# every per-dropback rate, from the same plays as their denominators
G_TGT_OF = G_COV0 + NC
G_TGT_MAN, G_TGT_ZONE, G_TCOV0 = G_TGT_OF + 1, G_TGT_OF + 2, G_TGT_OF + 3
G_W = G_TCOV0 + NC
# Per-game team context layout
(T_SNAPS, T_DB, T_ATT, T_TGT, T_AIR, T_CAR, T_RZT, T_RZC, T_PASS) = range(9)
# Per-game position allowance layout
(P_TGT, P_REC, P_RECY, P_RECTD, P_CAR, P_RUSHY, P_RUSHTD, P_PASSY, P_PASSTD, P_INT, P_FUML, P_STD, P_HALF, P_PPR) = range(14)


class Tallies:
    """Every counter for a set of games (one season's worth, normally)."""

    def __init__(self, season):
        self.season = season
        self.weeks = set()              # (season, week) of every game added -- the leakage proof
        self.n_games = 0
        self.dfn = {}                   # team -> {"pers", "box", "cov_db", "tgt", "run", "rz"}
        self.off = {}                   # team -> {"pers", "tot", "tgt", "carry"}
        self.qb = {}                    # gsis -> {"depth", "split", "tot", "tgt", "games"}
        self.pl = {}                    # gsis -> {"rec", "rush"}
        self.team_games = {}            # team -> [("off"|"def", game record)]
        self.player_games = {}          # gsis -> [game record line]

    def _dfn(self, t):
        d = self.dfn.get(t)
        if d is None:
            d = self.dfn[t] = {"pers": {}, "box": {}, "cov_db": {}, "tgt": {}, "run": {}, "rz": {}}
        return d

    def _off(self, t):
        d = self.off.get(t)
        if d is None:
            d = self.off[t] = {"pers": {}, "tot": [0.0] * 30, "tgt": {}, "carry": {}}
        return d

    def _qb(self, q):
        d = self.qb.get(q)
        if d is None:
            d = self.qb[q] = {"depth": {}, "split": {}, "tot": [0.0] * 14, "tgt": {}, "games": set()}
        return d

    def _pl(self, p):
        d = self.pl.get(p)
        if d is None:
            d = self.pl[p] = {"rec": {}, "rush": {}}
        return d


def game_ranges(store):
    """[(gi, start, stop)] play-index ranges per game, in store order."""
    gi = store["cols"]["gi"]
    out = []
    if not len(gi):
        return out
    start, cur = 0, gi[0]
    for i in range(1, len(gi)):
        if gi[i] != cur:
            out.append((cur, start, i))
            start, cur = i, gi[i]
    out.append((cur, start, len(gi)))
    return out


def add_game(T, store, gi, start, stop, positions):
    """Fold one game's plays into T. Reads the store's arrays directly (the
    hot loop of the whole engine; ~60 dict updates a play)."""
    C = store["cols"]
    teams = store["teams"]
    g = store["games"][gi]
    season, week = store["season"], g["week"]
    T.weeks.add((season, week))
    T.n_games += 1
    c_off, c_dfn, c_kind, c_db, c_yds, c_air, c_yac, c_epa = C["off"], C["dfn"], C["kind"], C["db"], C["yds"], C["air"], C["yac"], C["epa"]
    c_cpoe, c_xpass, c_succ, c_comp, c_ptd, c_rtd, c_int, c_fuml = C["cpoe"], C["xpass"], C["succ"], C["comp"], C["ptd"], C["rtd"], C["intc"], C["fuml"]
    c_down, c_ytg, c_yl, c_qtr, c_sd, c_sg, c_nh, c_ploc, c_rloc, c_rgap = C["down"], C["ytg"], C["yl"], C["qtr"], C["sd"], C["sg"], C["nh"], C["ploc"], C["rloc"], C["rgap"]
    c_passer, c_receiver, c_rusher, c_fumbler = C["passer"], C["receiver"], C["rusher"], C["fumbler"]
    c_part, c_ndl, c_nlb, c_ndb, c_box, c_rushers, c_ttt, c_press, c_route, c_mz, c_cov, c_opers, c_oform = (
        C["part"], C["ndl"], C["nlb"], C["ndb"], C["box"], C["rushers"], C["ttt"], C["press"], C["route"], C["mz"], C["cov"], C["opers"], C["oform"])
    c_ftn, c_motion, c_pa, c_screen, c_rpo, c_frush, c_fbox, c_catch, c_cont, c_drop, c_qbhit = (
        C["ftn"], C["motion"], C["pa"], C["screen"], C["rpo"], C["frush"], C["fbox"], C["catchable"], C["contested"], C["drop"], C["qbhit"])
    NA, NA_H = D.NA, D.NA_H
    # per-game records, one per offence side
    recs = {}

    def rec_for(off_t, dfn_t):
        r = recs.get(off_t)
        if r is None:
            r = recs[off_t] = {"season": season, "week": week, "gi": gi, "id": g["id"], "off": off_t, "dfn": dfn_t,
                               "pos": {}, "players": {}, "team": [0.0] * 9}
        return r

    def pline(r, pid):
        v = r["players"].get(pid)
        if v is None:
            v = r["players"][pid] = [0.0] * G_W
        return v

    def posline(r, pos):
        v = r["pos"].get(pos)
        if v is None:
            v = r["pos"][pos] = [0.0] * 14
        return v

    for i in range(start, stop):
        kind = c_kind[i]
        if kind in (D.KNEEL, D.SPIKE):
            continue
        off_t, dfn_t = teams[c_off[i]], teams[c_dfn[i]]
        Dd, Oo = T._dfn(dfn_t), T._off(off_t)
        r = rec_for(off_t, dfn_t)
        tm = r["team"]
        is_db = c_db[i] == 1
        is_run = kind == D.RUN
        is_att = kind == D.ATTEMPT
        yds = c_yds[i]
        epa = c_epa[i]
        epa_f = 0.0 if epa == NA_H else epa / 100.0
        succ = c_succ[i]
        succ_f = 1.0 if succ == 1 else 0.0
        down, ytg, yl = c_down[i], c_ytg[i], c_yl[i]
        ddb = D.dd_bucket(down, ytg)
        rz = yl <= 20
        xpass = c_xpass[i]
        part = c_part[i] == 1
        ftn = c_ftn[i] == 1
        opers = c_opers[i]
        ndl, nlb, ndb = (c_ndl[i], c_nlb[i], c_ndb[i]) if part else (NA, NA, NA)
        box = c_box[i] if part else NA
        if box == NA and ftn and c_fbox[i] > 0:
            box = c_fbox[i]               # FTN's box count stands in where NGS has none (2026)
        cov, mz = (c_cov[i], c_mz[i]) if part else (0, 0)
        frush = c_frush[i] if ftn else NA
        rushers = c_rushers[i] if part else NA
        blitz = NA
        if frush != NA and frush > 0:
            blitz = 1 if frush >= 5 else 0
        elif rushers != NA and rushers > 0:
            blitz = 1 if rushers >= 5 else 0
        press = c_press[i] if part else NA
        ttt = c_ttt[i] if part else NA
        sg = c_sg[i]
        tm[T_SNAPS] += 1
        if is_db:
            tm[T_DB] += 1
        if is_run:
            tm[T_CAR] += 1
            if rz:
                tm[T_RZC] += 1
        if is_db:
            tm[T_PASS] += 1
        # ---- defense: personnel vs offence personnel and situation
        _add(Dd["pers"], (opers, ndl, nlb, ndb, ddb), (1.0, 1.0 if is_db else 0.0))
        _add(Dd["box"], (box,), (1.0, 1.0 if is_run else 0.0, float(yds) if is_run else 0.0, succ_f if is_run else 0.0,
                                 epa_f if is_run else 0.0, 1.0 if is_db else 0.0))
        # ---- offence: personnel, formation, situation
        _add(Oo["pers"], (opers, c_oform[i] if part else 0), (1.0, 1.0 if is_db else 0.0))
        ot = Oo["tot"]
        ot[0] += 1
        if is_db:
            ot[1] += 1
        if xpass != NA:
            ot[2] += 1
            ot[3] += (1.0 if is_db else 0.0) - xpass / 1000.0
        neutral = c_qtr[i] <= 3 and -7 <= c_sd[i] <= 7
        if neutral:
            ot[4] += 1
            if is_db:
                ot[5] += 1
        if down in (1, 2):
            ot[6] += 1
            if is_db:
                ot[7] += 1
        ot[8] += sg
        ot[9] += c_nh[i]
        if ftn:
            ot[10] += 1
            if c_motion[i] == 1:
                ot[11] += 1
            if is_db:
                ot[12] += 1
                if c_pa[i] == 1:
                    ot[13] += 1
                if c_screen[i] == 1:
                    ot[14] += 1
            if c_rpo[i] == 1:
                ot[15] += 1
        if rz:
            ot[16] += 1
            if is_db:
                ot[17] += 1
        if is_db:
            if press != NA:
                ot[18] += 1
                ot[19] += press
            if kind == D.SACK:
                ot[20] += 1
            if blitz != NA:
                ot[21] += 1
                ot[22] += blitz
            if ttt != NA:
                ot[23] += 1
                ot[24] += ttt / 100.0
            if c_qbhit[i]:
                ot[25] += 1
        # ---- the ball carrier / passer / receiver
        if is_run or kind == D.SCRAMBLE:
            rp = c_rusher[i]
            if rp:
                pos = _pos(positions, rp)
                pl = pline(r, rp)
                pl[G_CAR] += 1
                pl[G_RUSHY] += yds
                if c_rtd[i]:
                    pl[G_RUSHTD] += 1
                if rz:
                    pl[G_RZC] += 1
                pp = posline(r, pos)
                pp[P_CAR] += 1
                pp[P_RUSHY] += yds
                if c_rtd[i]:
                    pp[P_RUSHTD] += 1
                if is_run:
                    _add(Oo["carry"], (pos,), (1.0, float(yds)))
                    bb = box_bucket(box)
                    key = (c_rloc[i], c_rgap[i], bb)
                    vals = (1.0, float(yds), succ_f, epa_f, 1.0 if yds <= 0 else 0.0, 1.0 if yds >= 10 else 0.0,
                            1.0 if c_rtd[i] else 0.0)
                    _add(T._pl(rp)["rush"], key, vals)
                    _add(Dd["run"], key, vals)
                    _add(Dd["rz"], (pos, "car"), (1.0 if rz else 0.0,))
        if is_db:
            q = c_passer[i]
            cov_key = (cov, mz)
            _add(Dd["cov_db"], cov_key, (1.0, 1.0 if kind == D.SACK else 0.0,
                                         float(press) if press != NA else 0.0, 1.0 if press != NA else 0.0,
                                         ttt / 100.0 if ttt != NA else 0.0, 1.0 if ttt != NA else 0.0,
                                         float(blitz) if blitz != NA else 0.0, 1.0 if blitz != NA else 0.0,
                                         float(rushers) if rushers != NA and rushers > 0 else 0.0,
                                         1.0 if rushers != NA and rushers > 0 else 0.0,
                                         1.0 if kind == D.SCRAMBLE else 0.0, epa_f))
            if q:
                Q = T._qb(q)
                Q["games"].add((season, week, gi))
                qt = Q["tot"]
                qt[0] += 1
                qt[4] += epa_f
                if kind == D.SACK:
                    qt[5] += 1
                if ttt != NA:
                    qt[7] += ttt / 100.0
                    qt[8] += 1
                if kind == D.SCRAMBLE:
                    qt[11] += 1
                    qt[12] += yds
                base = (1.0, 1.0 if is_att else 0.0, float(c_comp[i]) if is_att else 0.0,
                        float(yds) if c_comp[i] else 0.0, epa_f, 1.0 if kind == D.SACK else 0.0,
                        float(c_int[i]))
                if mz:
                    _add(Q["split"], ("mz", mz), base)
                if cov:
                    _add(Q["split"], ("cov", cov), base)
                if press != NA:
                    _add(Q["split"], ("press", press), base)
                if blitz != NA:
                    _add(Q["split"], ("blitz", blitz), base)
                pl = pline(r, q)
                if is_att:
                    pl[G_PATT] += 1
                    if c_comp[i]:
                        pl[G_PCMP] += 1
                        pl[G_PASSY] += yds
                    if c_ptd[i]:
                        pl[G_PASSTD] += 1
                    if c_int[i]:
                        pl[G_INT] += 1
                    pq = posline(r, _pos(positions, q))
                    if c_comp[i]:
                        pq[P_PASSY] += yds
                    if c_ptd[i]:
                        pq[P_PASSTD] += 1
                    if c_int[i]:
                        pq[P_INT] += 1
            if is_att:
                tm[T_ATT] += 1
                air = c_air[i]
                air_known = air != NA and air > -99
                depth = D.depth_bucket(air) if air_known else None
                rcv = c_receiver[i]
                cpoe = c_cpoe[i]
                if q:
                    Q = T._qb(q)
                    qt = Q["tot"]
                    qt[1] += 1
                    qt[2] += c_comp[i]
                    qt[3] += yds if c_comp[i] else 0
                    qt[6] += c_int[i]
                    if air_known:
                        qt[9] += 1.0 if air >= 20 else 0.0
                        qt[10] += 1
                    _add(Q["depth"], (depth,), (1.0, float(c_comp[i]), cpoe / 10.0 if cpoe != NA_H else 0.0,
                                                1.0 if cpoe != NA_H else 0.0, float(yds) if c_comp[i] else 0.0,
                                                epa_f, float(air) if air_known else 0.0))
                if rcv:
                    pos = _pos(positions, rcv)
                    comp = c_comp[i]
                    yac = c_yac[i]
                    yac_f = float(yac) if (comp and yac != NA and yac > -99) else 0.0
                    tm[T_TGT] += 1
                    if air_known:
                        tm[T_AIR] += air
                    if rz:
                        tm[T_RZT] += 1
                    if q:
                        _add(T._qb(q)["tgt"], (rcv,), (1.0, float(air) if air_known else 0.0))
                    # route families are built from the ROUTE_ERA charting only: the
                    # 2016-2022 vocabulary cuts the short game differently (nfl_matchup_data)
                    route = c_route[i] if (part and season >= D.ROUTE_ERA) else 0
                    rfam = D.ROUTE_FAMILY.get(D.ROUTES[route], "") if route else ""
                    loc = c_ploc[i]
                    cont = c_cont[i] if ftn else NA
                    catch = c_catch[i] if ftn else NA
                    drop = c_drop[i] if ftn else NA
                    vals = (1.0, float(comp), float(yds) if comp else 0.0, epa_f, yac_f,
                            float(air) if air_known else 0.0, 1.0 if c_ptd[i] else 0.0,
                            1.0 if cont == 1 else 0.0, 1.0 if cont != NA else 0.0,
                            1.0 if catch == 1 else 0.0, 1.0 if catch != NA else 0.0,
                            1.0 if drop == 1 else 0.0, 1.0 if (comp and yds >= 20) else 0.0,
                            1.0 if air_known else 0.0, float(c_int[i]))
                    key = (cov, mz, pos, depth, loc, rfam)
                    _add(Dd["tgt"], key, vals)
                    _add(T._pl(rcv)["rec"], key, vals)
                    _add(Oo["tgt"], (pos, depth, loc), (1.0, float(air) if air_known else 0.0))
                    _add(Dd["rz"], (pos, "tgt"), (1.0 if rz else 0.0,))
                    pl = pline(r, rcv)
                    pl[G_TGT] += 1
                    pl[G_REC] += comp
                    pl[G_RECY] += yds if comp else 0
                    if c_ptd[i]:
                        pl[G_RECTD] += 1
                    if air_known:
                        pl[G_AIR] += air
                    if rz:
                        pl[G_RZT] += 1
                    pp = posline(r, pos)
                    pp[P_TGT] += 1
                    pp[P_REC] += comp
                    pp[P_RECY] += yds if comp else 0
                    if c_ptd[i]:
                        pp[P_RECTD] += 1
        if c_fuml[i]:
            fp = c_fumbler[i]
            if fp:
                pline(r, fp)[G_FUML] += 1
                posline(r, _pos(positions, fp))[P_FUML] += 1
    # ---- close the game: points, on-field snaps, the records
    onfield = (store.get("onfield") or {}).get(gi) or {}
    for off_t, r in recs.items():
        for pid, pl in r["players"].items():
            line = {"rec": pl[G_REC], "rec_yds": pl[G_RECY], "rec_td": pl[G_RECTD], "rush_yds": pl[G_RUSHY],
                    "rush_td": pl[G_RUSHTD], "pass_yds": pl[G_PASSY], "pass_td": pl[G_PASSTD], "intc": pl[G_INT],
                    "fuml": pl[G_FUML]}
            pl[G_STD], pl[G_HALF], pl[G_PPR] = (fantasy_points(line, "std"), fantasy_points(line, "half"),
                                                fantasy_points(line, "ppr"))
        # every skill player on the field for this offence gets his snaps, a line
        # of zeros if he never touched the ball (a decoy's game still counts)
        for pid, v in onfield.items():
            if teams[v[D.OF_TEAM]] != off_t:
                continue
            pl = r["players"].get(pid)
            if pl is None:
                pl = r["players"][pid] = [0.0] * G_W
            pl[G_SNAPS], pl[G_DB], pl[G_RUSHSN], pl[G_MAN], pl[G_ZONE] = v[0], v[1], v[2], v[3], v[4]
            pl[G_TGT_OF], pl[G_TGT_MAN], pl[G_TGT_ZONE] = v[D.OF_TGT], v[D.OF_TGT_MAN], v[D.OF_TGT_ZONE]
            for k in range(NC):
                pl[G_COV0 + k] = v[D.OF_COV0 + k]
                pl[G_TCOV0 + k] = v[D.OF_TCOV0 + k]
        for pos, pp in r["pos"].items():
            line = {"rec": pp[P_REC], "rec_yds": pp[P_RECY], "rec_td": pp[P_RECTD], "rush_yds": pp[P_RUSHY],
                    "rush_td": pp[P_RUSHTD], "pass_yds": pp[P_PASSY], "pass_td": pp[P_PASSTD], "intc": pp[P_INT],
                    "fuml": pp[P_FUML]}
            pp[P_STD], pp[P_HALF], pp[P_PPR] = (fantasy_points(line, "std"), fantasy_points(line, "half"),
                                                fantasy_points(line, "ppr"))
        T.team_games.setdefault(off_t, []).append(("off", r))
        T.team_games.setdefault(r["dfn"], []).append(("def", r))
        for pid in r["players"]:
            T.player_games.setdefault(pid, []).append(r)
    return recs


def season_tallies(store, positions, max_week=None, min_week=None):
    """Tallies over a store's games with min_week <= week < max_week (either
    bound None for open). This is the as-of operation: max_week = the cutoff
    week keeps strictly earlier games."""
    T = Tallies(store["season"])
    for gi, start, stop in game_ranges(store):
        w = store["games"][gi]["week"]
        if max_week is not None and w >= max_week:
            continue
        if min_week is not None and w < min_week:
            continue
        add_game(T, store, gi, start, stop, positions)
    return T


# ---- reading profiles off tallies ---------------------------------------------
def _merge(tallies, getter):
    """{key: weighted sums} across (weight, Tallies) pairs; getter(T) -> dict."""
    out = {}
    for w, T in tallies:
        d = getter(T)
        if not d:
            continue
        for key, vals in d.items():
            cur = out.get(key)
            if cur is None:
                out[key] = [v * w for v in vals]
            else:
                for i, v in enumerate(vals):
                    cur[i] += v * w
    return out


def _sum_list(tallies, getter, width):
    out = [0.0] * width
    for w, T in tallies:
        lst = getter(T)
        if lst:
            for i, v in enumerate(lst):
                out[i] += v * w
    return out


def _games(tallies, getter):
    """[(weight, record)] across tallies, most recent last."""
    out = []
    for w, T in tallies:
        for item in getter(T) or []:
            out.append((w, item))
    return out


def _agg(d, select, width):
    """Sum the value lists of d over keys passing select."""
    out = [0.0] * width
    for key, vals in d.items():
        if select(key):
            for i, v in enumerate(vals):
                out[i] += v
    return out


def _tgt_block(vals, prior=None, metric="def_allow_target", adot_metric=None, yac_metric=None):
    """Per-target efficiency from a Dd['tgt'] / pl['rec'] value list. aDOT and
    YAC per reception take their own k when the caller has one measured
    (a player's aDOT is known at ~5 targets, his YPT at ~85)."""
    n, comp, yds, epa, yac, air, td, cont, cont_n, catch, catch_n, drop, expl, air_n, intc = vals
    k, _ = k_for(metric)
    ka = k_for(adot_metric)[0] if adot_metric else k
    ky = k_for(yac_metric)[0] if yac_metric else k
    p = prior or {}
    return {"targets": round(n, 2),
            "catch": rate(comp, n, p.get("catch"), k), "ypt": rate(yds, n, p.get("ypt"), k),
            "epa_t": rate(epa, n, p.get("epa_t"), k), "expl20": rate(expl, n, p.get("expl20"), k),
            "yac_rec": rate(yac, comp, p.get("yac_rec"), ky), "adot": rate(air, air_n, p.get("adot"), ka),
            "td_t": rate(td, n, p.get("td_t"), k), "int_t": rate(intc, n, p.get("int_t"), k),
            "contested": rate(cont, cont_n, p.get("contested"), k),
            "catchable": rate(catch, catch_n, p.get("catchable"), k)}


def _flat(block):
    """The eb values of a _tgt_block as a plain prior dict."""
    return {k: (v.get("eb") if isinstance(v, dict) else None) for k, v in block.items() if k != "targets"}


class Snapshot:
    """Profiles as of (season, week) from the current season's tallies and the
    prior seasons' at their weights."""

    def __init__(self, season, week, current, priors=(), decay=DECAY):
        self.season, self.week = int(season), int(week)
        self.weights = {}
        self.tallies = []
        if current is not None:
            self.tallies.append((1.0, current))
            self.weights[int(season)] = 1.0
        for T in priors:
            w = decay ** (int(season) - int(T.season))
            self.tallies.append((w, T))
            self.weights[int(T.season)] = round(w, 4)
        self.decay = decay
        self._league = None
        self._k_src = k_for("def_cov_share")[1]
        # leakage check: nothing from the cutoff week or later, nothing from a later season
        for _w, T in self.tallies:
            for s, wk in T.weeks:
                if s > self.season or (s == self.season and wk >= self.week):
                    raise ValueError(f"tallies hold {s} week {wk}, cutoff {self.season} week {self.week}")

    def contains(self, season, week):
        return any((season, week) in T.weeks for _w, T in self.tallies)

    def _stamp(self, d):
        d["as_of"] = [self.season, self.week]
        d["weights"] = dict(self.weights)
        d["k_source"] = self._k_src
        return d

    # ---- league priors
    def league(self):
        if self._league is not None:
            return self._league
        tgt = {}
        for w, T in self.tallies:
            for d in T.dfn.values():
                for key, vals in d["tgt"].items():
                    cur = tgt.get(key)
                    if cur is None:
                        tgt[key] = [v * w for v in vals]
                    else:
                        for i, v in enumerate(vals):
                            cur[i] += v * w
        cov_db, run, pers, box = {}, {}, {}, {}
        for w, T in self.tallies:
            for d in T.dfn.values():
                for src, dst in ((d["cov_db"], cov_db), (d["run"], run), (d["pers"], pers), (d["box"], box)):
                    for key, vals in src.items():
                        cur = dst.get(key)
                        if cur is None:
                            dst[key] = [v * w for v in vals]
                        else:
                            for i, v in enumerate(vals):
                                cur[i] += v * w
        L = {"tgt": tgt, "cov_db": cov_db, "run": run, "pers": pers, "box": box}
        allp = _agg(tgt, lambda k: True, 15)
        L["target"] = _flat(_tgt_block(allp))
        L["pos"] = {pos: _flat(_tgt_block(_agg(tgt, lambda k, p=pos: k[2] == p, 15))) for pos in POS}
        db_all = _agg(cov_db, lambda k: True, 12)
        charted = _agg(cov_db, lambda k: k[0] > 0, 12)[0]
        L["cov"] = {D.COVERAGE[c]: (_agg(cov_db, lambda k, c=c: k[0] == c, 12)[0] / charted if charted else None)
                    for c in range(1, len(D.COVERAGE))}
        mz_n = _agg(cov_db, lambda k: k[1] > 0, 12)[0]
        L["man"] = _agg(cov_db, lambda k: k[1] == 1, 12)[0] / mz_n if mz_n else None
        L["blitz"] = db_all[6] / db_all[7] if db_all[7] else None
        L["pressure"] = db_all[2] / db_all[3] if db_all[3] else None
        L["sack"] = db_all[1] / db_all[0] if db_all[0] else None
        L["ttt"] = db_all[4] / db_all[5] if db_all[5] else None
        L["scramble"] = db_all[10] / db_all[0] if db_all[0] else None
        L["epa_db"] = db_all[11] / db_all[0] if db_all[0] else None
        r_all = _agg(run, lambda k: True, 7)
        L["run"] = {"ypc": r_all[1] / r_all[0] if r_all[0] else None, "success": r_all[2] / r_all[0] if r_all[0] else None,
                    "epa_c": r_all[3] / r_all[0] if r_all[0] else None, "stuff": r_all[4] / r_all[0] if r_all[0] else None,
                    "expl10": r_all[5] / r_all[0] if r_all[0] else None}
        p_all = _agg(pers, lambda k: True, 2)
        p_known = _agg(pers, lambda k: k[3] != D.NA, 2)[0]
        L["nickel"] = _agg(pers, lambda k: k[3] == 5, 2)[0] / p_known if p_known else None
        L["dime"] = _agg(pers, lambda k: k[3] != D.NA and k[3] >= 6, 2)[0] / p_known if p_known else None
        L["base"] = _agg(pers, lambda k: k[3] != D.NA and k[3] <= 4, 2)[0] / p_known if p_known else None
        L["pass_rate"] = p_all[1] / p_all[0] if p_all[0] else None
        b_known = _agg(box, lambda k: k[0] != D.NA, 6)[0]
        L["light_box"] = _agg(box, lambda k: k[0] != D.NA and k[0] <= 6, 6)[0] / b_known if b_known else None
        L["heavy_box"] = _agg(box, lambda k: k[0] != D.NA and k[0] >= 8, 6)[0] / b_known if b_known else None
        # per-game position allowances league-wide (per team-game)
        games = 0.0
        tot = {pos: [0.0] * 14 for pos in POS}
        for w, T in self.tallies:
            for lst in T.team_games.values():
                for side, r in lst:
                    if side != "def":
                        continue
                    games += w
                    for pos, pp in r["pos"].items():
                        if pos in tot:
                            for i, v in enumerate(pp):
                                tot[pos][i] += v * w
        L["games"] = games
        L["pos_game"] = {pos: {"std": tot[pos][P_STD] / games if games else None,
                               "half": tot[pos][P_HALF] / games if games else None,
                               "ppr": tot[pos][P_PPR] / games if games else None,
                               "tgt": tot[pos][P_TGT] / games if games else None,
                               "rec_yds": tot[pos][P_RECY] / games if games else None,
                               "car": tot[pos][P_CAR] / games if games else None,
                               "rush_yds": tot[pos][P_RUSHY] / games if games else None,
                               "pass_yds": tot[pos][P_PASSY] / games if games else None}
                         for pos in POS}
        self._league = L
        return L

    # ---- defense
    def defense(self, team):
        L = self.league()
        pers = _merge(self.tallies, lambda T: (T.dfn.get(team) or {}).get("pers"))
        box = _merge(self.tallies, lambda T: (T.dfn.get(team) or {}).get("box"))
        cov_db = _merge(self.tallies, lambda T: (T.dfn.get(team) or {}).get("cov_db"))
        tgt = _merge(self.tallies, lambda T: (T.dfn.get(team) or {}).get("tgt"))
        run = _merge(self.tallies, lambda T: (T.dfn.get(team) or {}).get("run"))
        rz = _merge(self.tallies, lambda T: (T.dfn.get(team) or {}).get("rz"))
        games = [(w, r) for w, (side, r) in _games(self.tallies, lambda T: T.team_games.get(team)) if side == "def"]
        kp, _ = k_for("def_pers")
        kc, _ = k_for("def_cov_share")
        kb, _ = k_for("def_blitz")
        kr, _ = k_for("def_run")
        kbx, _ = k_for("def_box")
        kpr, _ = k_for("def_pressure")
        ksk, _ = k_for("def_sack")
        ktt, _ = k_for("def_ttt")
        kep, _ = k_for("def_epa_db")
        ksc, _ = k_for("def_scramble")
        out = {"team": team, "games": {"current": sum(1 for w, r in games if w == 1.0),
                                       "weighted": round(sum(w for w, r in games), 2)}}
        # personnel
        known = {k: v for k, v in pers.items() if k[3] != D.NA}
        n_known = sum(v[0] for v in known.values())
        groups = {}
        for (op, dl, lb, db, dd), v in known.items():
            _add(groups, f"{dl}-{lb}-{db}", v)
        top = sorted(groups.items(), key=lambda kv: -kv[1][0])
        base_43 = sum(v[0] for (op, dl, lb, db, dd), v in known.items() if op in (12, 21, 13, 22) and db <= 4 and dl == 4 and lb == 3)
        base_34 = sum(v[0] for (op, dl, lb, db, dd), v in known.items() if op in (12, 21, 13, 22) and db <= 4 and dl == 3 and lb == 4)
        by_op = {}
        for code, label in ((11, "11"), (12, "12"), (13, "13"), (10, "10"), (21, "21"), (22, "22"), (20, "20")):
            sel = {k: v for k, v in known.items() if k[0] == code}
            n = sum(v[0] for v in sel.values())
            if not n:
                continue
            by_op[label] = {"snaps": round(n, 1), "share": rate(n, n_known, None, None),
                            "pass_rate": rate(sum(v[1] for v in sel.values()), n, L["pass_rate"], kp),
                            "nickel": rate(sum(v[0] for k, v in sel.items() if k[3] == 5), n, L["nickel"], kp),
                            "dime": rate(sum(v[0] for k, v in sel.items() if k[3] >= 6), n, L["dime"], kp),
                            "base": rate(sum(v[0] for k, v in sel.items() if k[3] <= 4), n, L["base"], kp)}
        by_dd = {}
        for dd in ("early", "2nd_long", "3rd_short", "3rd_med", "3rd_long"):
            sel = {k: v for k, v in known.items() if k[4] == dd}
            n = sum(v[0] for v in sel.values())
            if not n:
                continue
            by_dd[dd] = {"snaps": round(n, 1),
                         "pass_rate": rate(sum(v[1] for v in sel.values()), n, L["pass_rate"], kp),
                         "nickel": rate(sum(v[0] for k, v in sel.items() if k[3] == 5), n, L["nickel"], kp),
                         "dime": rate(sum(v[0] for k, v in sel.items() if k[3] >= 6), n, L["dime"], kp),
                         "heavy_front": rate(sum(v[0] for k, v in sel.items() if k[3] <= 4), n, L["base"], kp)}
        out["personnel"] = {
            "snaps_charted": round(n_known, 1),
            "most_played": top[0][0] if top else None,
            "most_played_share": rate(top[0][1][0], n_known) if top else None,
            "groups": {g: {"snaps": round(v[0], 1), "share": round(v[0] / n_known, 4) if n_known else None,
                           "pass_rate": round(v[1] / v[0], 4) if v[0] else None} for g, v in top[:8]},
            # the nominal front is INFERRED from what the unit lines up in against
            # heavy offensive personnel with four defensive backs, and is kept apart
            # from what it plays most (nickel, nearly always)
            "base_front": ("4-3" if base_43 > base_34 else "3-4" if base_34 > base_43 else None),
            "base_front_n": {"4-3": round(base_43, 1), "3-4": round(base_34, 1)},
            "nickel": rate(sum(v[0] for k, v in known.items() if k[3] == 5), n_known, L["nickel"], kp),
            "dime": rate(sum(v[0] for k, v in known.items() if k[3] >= 6), n_known, L["dime"], kp),
            "base": rate(sum(v[0] for k, v in known.items() if k[3] <= 4), n_known, L["base"], kp),
            "by_off_personnel": by_op, "by_down_distance": by_dd}
        # box
        bk = {k: v for k, v in box.items() if k[0] != D.NA}
        nb = sum(v[0] for v in bk.values())
        def box_rows(sel):
            r_n = sum(v[1] for v in sel.values())
            return {"carries": round(r_n, 1), "ypc": rate(sum(v[2] for v in sel.values()), r_n, L["run"]["ypc"], kr),
                    "success": rate(sum(v[3] for v in sel.values()), r_n, L["run"]["success"], kr),
                    "epa_c": rate(sum(v[4] for v in sel.values()), r_n, L["run"]["epa_c"], kr),
                    "pass_rate": rate(sum(v[5] for v in sel.values()), sum(v[0] for v in sel.values()), L["pass_rate"], kbx)}
        out["box"] = {"snaps_charted": round(nb, 1),
                      "light": rate(sum(v[0] for k, v in bk.items() if k[0] <= 6), nb, L["light_box"], kbx),
                      "heavy": rate(sum(v[0] for k, v in bk.items() if k[0] >= 8), nb, L["heavy_box"], kbx),
                      "mean": rate(sum(k[0] * v[0] for k, v in bk.items()), nb),
                      "vs_run": {b: box_rows({k: v for k, v in bk.items() if box_bucket(k[0]) == b})
                                 for b in ("light", "neutral", "heavy")}}
        # coverage
        charted = {k: v for k, v in cov_db.items() if k[0] > 0}
        nc = sum(v[0] for v in charted.values())
        mzk = {k: v for k, v in cov_db.items() if k[1] > 0}
        nmz = sum(v[0] for v in mzk.values())
        fam = {}
        for (c, mz), v in charted.items():
            _add(fam, D.COV_FAMILY.get(D.COVERAGE[c], "other"), (v[0],))
        out["coverage"] = {
            "dropbacks_charted": round(nc, 1), "source": "NGS participation (post-snap charted)",
            "man": rate(sum(v[0] for k, v in mzk.items() if k[1] == 1), nmz, L["man"], kc),
            "zone": rate(sum(v[0] for k, v in mzk.items() if k[1] == 2), nmz, None if L["man"] is None else 1 - L["man"], kc),
            "single_high": rate(fam.get("single_high", [0.0])[0], nc, sum(L["cov"][c] or 0 for c in ("COVER_1", "COVER_3")) if nc else None, kc),
            "two_high": rate(fam.get("two_high", [0.0])[0], nc, sum(L["cov"][c] or 0 for c in ("COVER_2", "COVER_4", "COVER_6", "2_MAN")) if nc else None, kc),
            "zero": rate(fam.get("zero", [0.0])[0], nc, L["cov"].get("COVER_0"), kc),
            "other": rate(fam.get("other", [0.0])[0], nc, sum(L["cov"][c] or 0 for c in ("COVER_9", "COMBO", "BLOWN")) if nc else None, kc),
            "families": {D.COVERAGE[c]: rate(sum(v[0] for k, v in charted.items() if k[0] == c), nc, L["cov"][D.COVERAGE[c]], kc)
                         for c in range(1, len(D.COVERAGE))}}
        # pressure
        db_all = _agg(cov_db, lambda k: True, 12)
        out["pressure"] = {"dropbacks": round(db_all[0], 1),
                           "blitz": rate(db_all[6], db_all[7], L["blitz"], kb),
                           "blitz_def": "5+ pass rushers (FTN n_pass_rushers; NGS number_of_pass_rushers where FTN is absent)",
                           "pressure": rate(db_all[2], db_all[3], L["pressure"], kpr),
                           "sack": rate(db_all[1], db_all[0], L["sack"], ksk),
                           "scramble": rate(db_all[10], db_all[0], L["scramble"], ksc),
                           "ttt": rate(db_all[4], db_all[5], L["ttt"], ktt),
                           "rushers": rate(db_all[8], db_all[9], None, None),
                           "epa_db": rate(db_all[11], db_all[0], L["epa_db"], kep)}
        # allowed
        lt = L["target"]
        allowed = {"all": _tgt_block(_agg(tgt, lambda k: True, 15), lt)}
        allowed["per_coverage"] = {D.COVERAGE[c]: _tgt_block(_agg(tgt, lambda k, c=c: k[0] == c, 15), lt)
                                   for c in range(1, len(D.COVERAGE))}
        allowed["per_mz"] = {"man": _tgt_block(_agg(tgt, lambda k: k[1] == 1, 15), lt),
                             "zone": _tgt_block(_agg(tgt, lambda k: k[1] == 2, 15), lt)}
        allowed["per_pos"] = {}
        for pos in POS:
            blk = _tgt_block(_agg(tgt, lambda k, p=pos: k[2] == p, 15), L["pos"][pos])
            blk["tgt_per_db"] = rate(blk["targets"], db_all[0], None, None)
            blk["vs_man"] = _tgt_block(_agg(tgt, lambda k, p=pos: k[2] == p and k[1] == 1, 15), _flat(blk))
            blk["vs_zone"] = _tgt_block(_agg(tgt, lambda k, p=pos: k[2] == p and k[1] == 2, 15), _flat(blk))
            blk["per_depth"] = {D.DEPTH[d]: _tgt_block(_agg(tgt, lambda k, p=pos, d=d: k[2] == p and k[3] == d, 15), _flat(blk))
                                for d in range(4)}
            allowed["per_pos"][pos] = blk
        allowed["per_depth"] = {D.DEPTH[d]: _tgt_block(_agg(tgt, lambda k, d=d: k[3] == d, 15), lt) for d in range(4)}
        allowed["per_location"] = {D.SIDE[s]: _tgt_block(_agg(tgt, lambda k, s=s: k[4] == s, 15), lt) for s in range(1, 4)}
        allowed["per_route"] = {f: _tgt_block(_agg(tgt, lambda k, f=f: k[5] == f, 15), lt)
                                for f in ("quick", "screen", "intermediate", "vertical", "other")}
        out["allowed"] = allowed
        # run defense
        r_all = _agg(run, lambda k: True, 7)
        def run_rows(vals):
            n = vals[0]
            return {"carries": round(n, 1), "ypc": rate(vals[1], n, L["run"]["ypc"], kr),
                    "success": rate(vals[2], n, L["run"]["success"], kr), "epa_c": rate(vals[3], n, L["run"]["epa_c"], kr),
                    "stuff": rate(vals[4], n, L["run"]["stuff"], kr), "expl10": rate(vals[5], n, L["run"]["expl10"], kr)}
        out["run"] = run_rows(r_all)
        out["run"]["by_direction"] = {}
        for s in range(1, 4):
            for gp in range(1, 4):
                vals = _agg(run, lambda k, s=s, gp=gp: k[0] == s and k[1] == gp, 7)
                if vals[0]:
                    out["run"]["by_direction"][f"{D.SIDE[s]}_{D.GAP[gp]}"] = run_rows(vals)
        # position allowances per game, raw and opponent-adjusted
        out["fantasy_allowed"] = self._pos_allowed(games, L)
        out["red_zone"] = {pos: {"targets": round((rz.get((pos, "tgt")) or [0.0])[0], 1),
                                 "carries": round((rz.get((pos, "car")) or [0.0])[0], 1)} for pos in POS}
        # recent
        recent = sorted(games, key=lambda wr: (wr[1]["season"], wr[1]["week"]))[-RECENT:]
        out["recent"] = {"games": len(recent), "fantasy_allowed": self._pos_allowed([(1.0, r) for w, r in recent], L, adjust=False)}
        return self._stamp(out)

    def _pos_allowed(self, games, L, adjust=True):
        """Per-game position allowances (raw per game, per target / per carry,
        and opponent-adjusted: allowed minus what that offence averaged against
        everyone else as of the cutoff)."""
        kg, _ = k_for("def_allow_game")
        out = {}
        wsum = sum(w for w, r in games)
        for pos in POS:
            tot = [0.0] * 14
            adj = {"std": 0.0, "half": 0.0, "ppr": 0.0, "tgt": 0.0, "rec_yds": 0.0, "rush_yds": 0.0, "pass_yds": 0.0}
            adj_w = 0.0
            for w, r in games:
                pp = r["pos"].get(pos) or [0.0] * 14
                for i, v in enumerate(pp):
                    tot[i] += v * w
                if adjust:
                    base = self._offense_avg_excluding(r["off"], pos, r)
                    if base is not None:
                        adj_w += w
                        adj["std"] += w * (pp[P_STD] - base[P_STD])
                        adj["half"] += w * (pp[P_HALF] - base[P_HALF])
                        adj["ppr"] += w * (pp[P_PPR] - base[P_PPR])
                        adj["tgt"] += w * (pp[P_TGT] - base[P_TGT])
                        adj["rec_yds"] += w * (pp[P_RECY] - base[P_RECY])
                        adj["rush_yds"] += w * (pp[P_RUSHY] - base[P_RUSHY])
                        adj["pass_yds"] += w * (pp[P_PASSY] - base[P_PASSY])
            lp = L["pos_game"][pos]
            blk = {"games": round(wsum, 2),
                   "per_game": {"std": rate(tot[P_STD], wsum, lp["std"], kg), "half": rate(tot[P_HALF], wsum, lp["half"], kg),
                                "ppr": rate(tot[P_PPR], wsum, lp["ppr"], kg), "targets": rate(tot[P_TGT], wsum, lp["tgt"], kg),
                                "rec_yds": rate(tot[P_RECY], wsum, lp["rec_yds"], kg), "carries": rate(tot[P_CAR], wsum, lp["car"], kg),
                                "rush_yds": rate(tot[P_RUSHY], wsum, lp["rush_yds"], kg), "pass_yds": rate(tot[P_PASSY], wsum, lp["pass_yds"], kg)},
                   "per_opportunity": {"catch": rate(tot[P_REC], tot[P_TGT]), "ypt": rate(tot[P_RECY], tot[P_TGT]),
                                       "ypc": rate(tot[P_RUSHY], tot[P_CAR]), "ppr_per_touch": rate(tot[P_PPR], tot[P_TGT] + tot[P_CAR])}}
            if adjust:
                blk["opp_adjusted"] = {"games": round(adj_w, 2),
                                       "basis": "allowed minus the offence's per-game average against every other defense, as of the cutoff",
                                       **{k: rate(v, adj_w, 0.0, kg) for k, v in adj.items()}}
            out[pos] = blk
        return out

    def _offense_avg_excluding(self, team, pos, exclude):
        """The offence's weighted per-game position line over its games as of
        the cutoff, excluding one record; None with fewer than two others."""
        tot = [0.0] * 14
        wsum = 0.0
        for w, (side, r) in _games(self.tallies, lambda T: T.team_games.get(team)):
            if side != "off" or r is exclude:
                continue
            pp = r["pos"].get(pos)
            wsum += w
            if pp:
                for i, v in enumerate(pp):
                    tot[i] += v * w
        if wsum < 2.0:
            return None
        return [v / wsum for v in tot]

    # ---- offense
    def offense(self, team):
        L = self.league()
        ko, _ = k_for("off_rate")
        pers = _merge(self.tallies, lambda T: (T.off.get(team) or {}).get("pers"))
        tot = _sum_list(self.tallies, lambda T: (T.off.get(team) or {}).get("tot"), 30)
        tgt = _merge(self.tallies, lambda T: (T.off.get(team) or {}).get("tgt"))
        carry = _merge(self.tallies, lambda T: (T.off.get(team) or {}).get("carry"))
        games = [(w, r) for w, (side, r) in _games(self.tallies, lambda T: T.team_games.get(team)) if side == "off"]
        snaps, db = tot[0], tot[1]
        known = {k: v for k, v in pers.items() if k[0] != D.PERS_UNKNOWN}
        nk = sum(v[0] for v in known.values())
        by_code = {}
        for (code, form), v in known.items():
            _add(by_code, code, v)
        forms = {}
        for (code, form), v in pers.items():
            if form:
                _add(forms, D.FORMATION[form], v)
        nf = sum(v[0] for v in forms.values())
        n_t = sum(v[0] for v in tgt.values())
        out = {"team": team, "games": {"current": sum(1 for w, r in games if w == 1.0), "weighted": round(sum(w for w, r in games), 2)},
               "snaps": round(snaps, 1), "dropbacks": round(db, 1),
               "plays_per_game": rate(snaps, sum(w for w, r in games)),
               "pass_rate": rate(db, snaps, L["pass_rate"], ko),
               "proe": rate(tot[3], tot[2], 0.0, ko),
               "neutral_pass_rate": rate(tot[5], tot[4], L["pass_rate"], ko),
               "early_down_pass_rate": rate(tot[7], tot[6], L["pass_rate"], ko),
               "shotgun": rate(tot[8], snaps, None, None), "under_center": rate(snaps - tot[8], snaps, None, None),
               "no_huddle": rate(tot[9], snaps, None, None),
               "play_action": rate(tot[13], tot[12], None, None), "screen": rate(tot[14], tot[12], None, None),
               "motion": rate(tot[11], tot[10], None, None), "rpo": rate(tot[15], tot[10], None, None),
               "red_zone_pass_rate": rate(tot[17], tot[16], L["pass_rate"], ko),
               "personnel": {f"{c:02d}": {"snaps": round(v[0], 1), "share": round(v[0] / nk, 4) if nk else None,
                                          "pass_rate": round(v[1] / v[0], 4) if v[0] else None}
                             for c, v in sorted(by_code.items(), key=lambda kv: -kv[1][0])[:8]},
               "formation": {f: {"share": round(v[0] / nf, 4) if nf else None, "pass_rate": round(v[1] / v[0], 4) if v[0] else None}
                             for f, v in forms.items()},
               "protection": {"pressure_allowed": rate(tot[19], tot[18], L["pressure"], ko), "sack": rate(tot[20], db, L["sack"], ko),
                              "blitz_faced": rate(tot[22], tot[21], L["blitz"], ko), "ttt": rate(tot[24], tot[23], L["ttt"], ko),
                              "qb_hit": rate(tot[25], db, None, None)},
               "targets": {"n": round(n_t, 1),
                           "by_pos": {pos: rate(sum(v[0] for k, v in tgt.items() if k[0] == pos), n_t) for pos in POS},
                           "by_depth": {D.DEPTH[d]: rate(sum(v[0] for k, v in tgt.items() if k[1] == d), n_t) for d in range(4)},
                           "by_location": {D.SIDE[s]: rate(sum(v[0] for k, v in tgt.items() if k[2] == s), n_t) for s in range(1, 4)},
                           "deep_rate": rate(sum(v[0] for k, v in tgt.items() if k[1] == 3), sum(v[0] for k, v in tgt.items() if k[1] is not None))},
               "carries": {"n": round(sum(v[0] for v in carry.values()), 1),
                           "by_pos": {pos: rate((carry.get((pos,)) or [0.0])[0], sum(v[0] for v in carry.values())) for pos in POS}}}
        return self._stamp(out)

    # ---- quarterback
    def qb(self, pid):
        L = self.league()
        kq, _ = k_for("qb_rate")
        kd, _ = k_for("qb_depth")
        kqt, _ = k_for("qb_ttt")
        kqd, _ = k_for("qb_deep")
        depth = _merge(self.tallies, lambda T: (T.qb.get(pid) or {}).get("depth"))
        split = _merge(self.tallies, lambda T: (T.qb.get(pid) or {}).get("split"))
        tot = _sum_list(self.tallies, lambda T: (T.qb.get(pid) or {}).get("tot"), 14)
        tgt = _merge(self.tallies, lambda T: (T.qb.get(pid) or {}).get("tgt"))
        games = set()
        for w, T in self.tallies:
            games |= (T.qb.get(pid) or {}).get("games", set())
        db, att = tot[0], tot[1]
        n_t = sum(v[0] for v in tgt.values())
        def sp(vals, prior_epa):
            n = vals[0]
            return {"dropbacks": round(n, 1), "epa_db": rate(vals[4], n, prior_epa, kq),
                    "comp": rate(vals[2], vals[1], tot[2] / att if att else None, kq),
                    "ypa": rate(vals[3], vals[1], tot[3] / att if att else None, kq),
                    "sack": rate(vals[5], n, tot[5] / db if db else None, kq)}
        own_epa = (tot[4] + kq * (L["epa_db"] or 0.0)) / (db + kq) if (db or kq) else None
        out = {"gsis": D.gsis_str(pid), "games": len(games), "dropbacks": round(db, 1), "attempts": round(att, 1),
               "comp": rate(tot[2], att, None, None), "ypa": rate(tot[3], att, None, None),
               "epa_db": rate(tot[4], db, L["epa_db"], kq), "sack": rate(tot[5], db, L["sack"], kq),
               "int": rate(tot[6], att, None, None), "ttt": rate(tot[7], tot[8], L["ttt"], kqt),
               "deep_rate": rate(tot[9], tot[10], None, kqd), "scramble": rate(tot[11], db, L["scramble"], kq),
               "by_depth": {}, "vs": {}}
        for d in range(4):
            v = depth.get((d,))
            if v:
                out["by_depth"][D.DEPTH[d]] = {"attempts": round(v[0], 1), "comp": rate(v[1], v[0], None, None),
                                               "cpoe": rate(v[2], v[3], 0.0, kd), "ypa": rate(v[4], v[0], None, None),
                                               "epa_att": rate(v[5], v[0], None, None)}
        for name, key in (("man", ("mz", 1)), ("zone", ("mz", 2)), ("pressure", ("press", 1)), ("no_pressure", ("press", 0)),
                          ("blitz", ("blitz", 1)), ("no_blitz", ("blitz", 0))):
            v = split.get(key)
            if v:
                out["vs"][name] = sp(v, own_epa)
        for c in range(1, len(D.COVERAGE)):
            v = split.get(("cov", c))
            if v and v[0] >= 1:
                out["vs"][D.COVERAGE[c]] = sp(v, own_epa)
        out["targets"] = {"n": round(n_t, 1),
                          "top": [{"gsis": D.gsis_str(k[0]), "share": round(v[0] / n_t, 4), "adot": round(v[1] / v[0], 2)}
                                  for k, v in sorted(tgt.items(), key=lambda kv: -kv[1][0])[:8]] if n_t else []}
        return self._stamp(out)

    def qb_target_share(self, qb_pid, rcv_pid):
        tgt = _merge(self.tallies, lambda T: (T.qb.get(qb_pid) or {}).get("tgt"))
        n = sum(v[0] for v in tgt.values())
        v = tgt.get((rcv_pid,))
        return rate(v[0] if v else 0.0, n)

    # ---- skill player
    def player(self, pid, team=None):
        """The receiving / rushing profile of a skill player. `team` restricts
        the team-share context to games for that team (a traded player's old
        team's targets are not his new team's)."""
        L = self.league()
        kt, _ = k_for("pl_target")
        kc, _ = k_for("pl_carry")
        rec = _merge(self.tallies, lambda T: (T.pl.get(pid) or {}).get("rec"))
        rush = _merge(self.tallies, lambda T: (T.pl.get(pid) or {}).get("rush"))
        games = [(w, r) for w, r in _games(self.tallies, lambda T: T.player_games.get(pid))
                 if team is None or r["off"] == team]
        games.sort(key=lambda wr: (wr[1]["season"], wr[1]["week"]))
        wsum = sum(w for w, r in games)
        # weighted sums of the per-game line and the team context; db_cur counts
        # this season's on-field dropbacks alone (0 for a season with no participation)
        pl = [0.0] * G_W
        tm = [0.0] * 9
        db_cur = 0.0
        for w, r in games:
            line = r["players"].get(pid)
            if line:
                for i, v in enumerate(line):
                    pl[i] += v * w
                if w == 1.0 and r["season"] == self.season:
                    db_cur += line[G_DB]
            for i, v in enumerate(r["team"]):
                tm[i] += v * w
        all_t = _agg(rec, lambda k: True, 15)

        def shell_rate(fams):
            codes = [D.COV_IX[c] for c in fams]
            return rate(sum(pl[G_TCOV0 + c - 1] for c in codes), sum(pl[G_COV0 + c - 1] for c in codes), None, None)
        SH, TH = ("COVER_1", "COVER_3"), ("COVER_2", "COVER_4", "COVER_6", "2_MAN")
        OTH = tuple(c for c, f in D.COV_FAMILY.items() if f == "other")
        out = {"gsis": D.gsis_str(pid), "games": {"current": sum(1 for w, r in games if w == 1.0), "weighted": round(wsum, 2)},
               "team": team}
        # usage: every figure here is TEAM-SCOPED when `team` is given -- the games the
        # player played for that team, numerator and denominator alike. The
        # per-dropback rates count a target only on a play whose on-field list has
        # him, so they are <= 1 by construction and a season without participation
        # (2026) adds to neither side (n_current says how much of this season is in).
        out["usage"] = {
            "scope": ("team-scoped: the player's games for %s" % team) if team else "player: all his games in the window",
            "snap_share": rate(pl[G_SNAPS], tm[T_SNAPS], None, None),
            "snaps_source": "NGS participation on-field ids (2016-2025); none for 2026 -- see snap_counts in the dataset",
            "target_share": rate(pl[G_TGT], tm[T_TGT], None, None),
            "air_share": rate(pl[G_AIR], tm[T_AIR], None, None),
            "carry_share": rate(pl[G_CAR], tm[T_CAR], None, None),
            "rz_target_share": rate(pl[G_RZT], tm[T_RZT], None, None),
            "rz_carry_share": rate(pl[G_RZC], tm[T_RZC], None, None),
            "targets_pg": rate(pl[G_TGT], wsum, None, None), "carries_pg": rate(pl[G_CAR], wsum, None, None),
            "tgt_per_db": {**rate(pl[G_TGT_OF], pl[G_DB], None, None), "kind": "proxy", "n_current": db_cur,
                           "basis": "targets per dropback the player was on the field for (not routes run); "
                                    "numerator and denominator from the same charted plays"},
            "tgt_per_db_vs_man": rate(pl[G_TGT_MAN], pl[G_MAN], None, None),
            "tgt_per_db_vs_zone": rate(pl[G_TGT_ZONE], pl[G_ZONE], None, None),
            "tgt_per_db_by_shell": {"single_high": shell_rate(SH), "two_high": shell_rate(TH),
                                    "zero": shell_rate(("COVER_0",)), "other": shell_rate(OTH)}}
        # receiving efficiency, overall then by split with the player's own figure as the prior
        own = _tgt_block(all_t, L["target"], metric="pl_target", adot_metric="pl_adot", yac_metric="pl_yac")
        own_flat = _flat(own)
        out["receiving"] = own
        # efficiency pools every team the player played for in the window: it is
        # his, not the team's (the review's F1 was team-scoped denominators under
        # player-scoped numerators; the two scopes are now named and kept apart)
        out["receiving"]["scope"] = "player: all his teams in the window; usage above is team-scoped"
        out["receiving"]["adot"] = rate(all_t[5], all_t[13], L["target"]["adot"], k_for("pl_adot")[0])
        out["receiving"]["depth_mix"] = {D.DEPTH[d]: rate(_agg(rec, lambda k, d=d: k[3] == d, 15)[0], all_t[13]) for d in range(4)}
        out["receiving"]["location_mix"] = {D.SIDE[s]: rate(_agg(rec, lambda k, s=s: k[4] == s, 15)[0], _agg(rec, lambda k: k[4] > 0, 15)[0]) for s in range(1, 4)}
        charted_routes = _agg(rec, lambda k: k[5] != "", 15)[0]
        out["receiving"]["route_mix"] = {f: rate(_agg(rec, lambda k, f=f: k[5] == f, 15)[0], charted_routes)
                                         for f in ("quick", "screen", "intermediate", "vertical", "other")}
        out["receiving"]["route_source"] = "NGS route of the TARGETED receiver only (participation); no routes on untargeted snaps"
        out["receiving"]["vs_man"] = _tgt_block(_agg(rec, lambda k: k[1] == 1, 15), own_flat, metric="pl_target", adot_metric="pl_adot", yac_metric="pl_yac")
        out["receiving"]["vs_zone"] = _tgt_block(_agg(rec, lambda k: k[1] == 2, 15), own_flat, metric="pl_target", adot_metric="pl_adot", yac_metric="pl_yac")
        out["receiving"]["per_coverage"] = {}
        for c in range(1, len(D.COVERAGE)):
            v = _agg(rec, lambda k, c=c: k[0] == c, 15)
            if v[0] >= 1 or pl[G_COV0 + c - 1] >= 1:
                blk = _tgt_block(v, own_flat, metric="pl_target", adot_metric="pl_adot", yac_metric="pl_yac")
                # the target RATE under this coverage is team-scoped like the rest of usage
                blk["tgt_per_db"] = rate(pl[G_TCOV0 + c - 1], pl[G_COV0 + c - 1], None, None)
                out["receiving"]["per_coverage"][D.COVERAGE[c]] = blk
        out["receiving"]["per_depth"] = {D.DEPTH[d]: _tgt_block(_agg(rec, lambda k, d=d: k[3] == d, 15), own_flat, metric="pl_target", adot_metric="pl_adot", yac_metric="pl_yac") for d in range(4)}
        # the alignment PROXY -- never charted alignment
        mid = _agg(rec, lambda k: k[4] == 2, 15)[0]
        side_n = _agg(rec, lambda k: k[4] > 0, 15)[0]
        out["alignment_proxy"] = {
            "kind": "proxy", "value": rate(mid, side_n, None, None),
            "basis": ("share of the player's targets thrown to the middle third (nflfastR pass_location) plus his "
                      "route mix; slot/wide alignment is in no free source and this is NOT it"),
            "route_mix": out["receiving"]["route_mix"]}
        # rushing
        r_all = _agg(rush, lambda k: True, 7)
        def rr(vals, prior):
            n = vals[0]
            return {"carries": round(n, 1), "ypc": rate(vals[1], n, prior["ypc"], kc), "success": rate(vals[2], n, prior["success"], kc),
                    "epa_c": rate(vals[3], n, prior["epa_c"], kc), "stuff": rate(vals[4], n, prior["stuff"], kc),
                    "expl10": rate(vals[5], n, prior["expl10"], kc), "td_c": rate(vals[6], n, None, None)}
        own_r = rr(r_all, L["run"])
        own_r_flat = {k: (v["eb"] if isinstance(v, dict) and "eb" in v else None) for k, v in own_r.items() if k != "carries"}
        for k in ("ypc", "success", "epa_c", "stuff", "expl10"):
            own_r_flat.setdefault(k, L["run"][k])
        out["rushing"] = own_r
        out["rushing"]["by_box"] = {b: rr(_agg(rush, lambda k, b=b: k[2] == b, 7), own_r_flat) for b in ("light", "neutral", "heavy")}
        out["rushing"]["by_direction"] = {}
        for s in range(1, 4):
            for gp in range(1, 4):
                v = _agg(rush, lambda k, s=s, gp=gp: k[0] == s and k[1] == gp, 7)
                if v[0] >= 1:
                    out["rushing"]["by_direction"][f"{D.SIDE[s]}_{D.GAP[gp]}"] = rr(v, own_r_flat)
        # fantasy, season and recent
        def fp(gs):
            ws = sum(w for w, r in gs)
            s = [0.0, 0.0, 0.0]
            for w, r in gs:
                line = r["players"].get(pid)
                if line:
                    s[0] += line[G_STD] * w
                    s[1] += line[G_HALF] * w
                    s[2] += line[G_PPR] * w
            return {"games": round(ws, 2), "std": rate(s[0], ws), "half": rate(s[1], ws), "ppr": rate(s[2], ws)}
        out["fantasy"] = fp(games)
        recent = games[-RECENT:]
        rpl = [0.0] * G_W
        rtm = [0.0] * 9
        for w, r in recent:
            line = r["players"].get(pid)
            if line:
                for i, v in enumerate(line):
                    rpl[i] += v
            for i, v in enumerate(r["team"]):
                rtm[i] += v
        out["recent"] = {"games": len(recent), "fantasy": fp([(1.0, r) for w, r in recent]),
                         "target_share": rate(rpl[G_TGT], rtm[T_TGT]), "carry_share": rate(rpl[G_CAR], rtm[T_CAR]),
                         "snap_share": rate(rpl[G_SNAPS], rtm[T_SNAPS]), "targets_pg": rate(rpl[G_TGT], len(recent)),
                         "air_share": rate(rpl[G_AIR], rtm[T_AIR])}
        return self._stamp(out)


# ---- building a snapshot from the stores -------------------------------------
def positions_from_ids(ids_payload):
    """{gsis: pos} from the ids store payload."""
    ids = (ids_payload or {}).get("ids") or {}
    return {pid: r["pos"] for pid, r in ids.items()}


def build(stores, season, week, positions, decay=DECAY, back=BACK):
    """The snapshot as of (season, week) from {season: store}: the cutoff
    season's games with week < `week`, and the `back` prior seasons whole."""
    cur = stores.get(int(season))
    current = season_tallies(cur, positions, max_week=int(week)) if cur else None
    priors = []
    for s in range(int(season) - back, int(season)):
        st = stores.get(s)
        if st:
            priors.append(season_tallies(st, positions))
    return Snapshot(season, week, current, priors, decay=decay)
