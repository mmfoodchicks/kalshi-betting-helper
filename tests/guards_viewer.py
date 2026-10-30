"""Guards for the NFL game viewer (2026-10-29, nfl_viewer.py, static/nflview.js;
design and review with a Fable agent, research/reports/nfl_viewer.md): the
deep engine's passive play log (every box identical with and without it), the
legal game it records, the sack that can no longer carry the ball past the
goal line, the typical game's definition, the names (Sleeper's shares, the
right side, defenders only on certain events, nobody ruled out), the box
score the names must add up to, the per-down table, the piece and the route
(reads only; a build only in a niced child; pre-game only), the PC wiring and
the page's script (no inline handlers, every server string escaped).
Hermetic: boardshare and the deep store point at a temporary directory; the
Sleeper rows, the schedule and the profiles are stubs; nothing is fetched.

    python3 tests/guards_viewer.py       # exits 1 on any failed check
    from tests.guards_viewer import run  # run(ck) inside the main suite
"""
import contextlib
import copy
import inspect
import json
import os
import pickle
import random
import shutil
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)


@contextlib.contextmanager
def _sandbox():
    import boardshare
    import deep_cache
    tmp = tempfile.mkdtemp()
    saved = (boardshare._DIR, deep_cache.CACHE_DIR)
    try:
        boardshare._DIR = os.path.join(tmp, "boards")
        deep_cache.CACHE_DIR = os.path.join(tmp, "deep")
        os.makedirs(deep_cache.CACHE_DIR, exist_ok=True)
        yield tmp
    finally:
        boardshare._DIR, deep_cache.CACHE_DIR = saved
        shutil.rmtree(tmp, ignore_errors=True)


@contextlib.contextmanager
def _patched(*triples):
    old = [(o, n, getattr(o, n)) for o, n, _v in triples]
    try:
        for o, n, v in triples:
            setattr(o, n, v)
        yield
    finally:
        for o, n, v in reversed(old):
            setattr(o, n, v)


def _row(pid, first, last, pos, team, injury=None, **st):
    return {"player_id": str(pid), "team": team, "stats": st,
            "player": {"first_name": first, "last_name": last, "position": pos, "injury_status": injury}}


def _stub_rows(home="GB", away="CAR"):
    """Sleeper-shaped rows for both clubs with every role filled, plus one
    receiver tagged Out with the biggest projection on his team."""
    off, idp, k = [], [], []
    for t, b in ((home, 100), (away, 200)):
        off += [_row(b + 1, "Quinn", f"Passer{t}", "QB", t, pass_att=34, rush_att=3, rush_td=0.2),
                _row(b + 2, "Rex", f"Runner{t}", "RB", t, rush_att=16, rush_td=0.6, rec=3, rec_tgt=4, rec_td=0.1,
                     def_kr_yd=10),
                _row(b + 3, "Will", f"Wide{t}", "WR", t, rec=6, rec_tgt=9, rec_td=0.4, pr_yd=8),
                _row(b + 4, "Tad", f"Tight{t}", "TE", t, rec=4, rec_tgt=5, rec_td=0.3),
                _row(b + 5, "Ollie", f"Hurt{t}", "WR", t, injury="Out", rec=9, rec_tgt=12, rec_td=1.0, rush_att=5,
                     def_kr_yd=40, pr_yd=30)]
        idp += [_row(b + 11, "Dee", f"Lineman{t}", "DE", t, idp_tkl_solo=2, idp_sack=0.6, idp_ff=0.1, idp_fum_rec=0.05),
                _row(b + 12, "Lee", f"Backer{t}", "LB", t, idp_tkl_solo=5, idp_sack=0.2, idp_int=0.05, idp_ff=0.05,
                     idp_fum_rec=0.1),
                _row(b + 13, "Cory", f"Corner{t}", "CB", t, idp_tkl_solo=3, idp_int=0.1, idp_fum_rec=0.03, def_pr_yd=5),
                _row(b + 14, "Sid", f"Sidelined{t}", "S", t, injury="IR", idp_tkl_solo=9, idp_int=0.5, idp_sack=1.0,
                     idp_ff=0.5, idp_fum_rec=0.5)]
        k += [_row(b + 21, "Kip", f"Kicker{t}", "K", t, fga=2, xpa=3)]
    return off, idp, k


_BOX = {"pass_att": "pa", "comp": "pc", "pass_yds": "py", "td_pass": "ptd", "int": "pi", "sacks": "sk",
        "sack_yds": "sky", "rush_att": "ra", "rush_yds": "ry", "td_rush": "rtd", "fum_lost": "fl",
        "fg_att": "fga", "fg_made": "fgm", "xp_made": "xpm"}
_OFF_STATS = {"pa", "pc", "py", "ptd", "pi", "sk", "sky", "ra", "ry", "rtd", "tg", "rc", "rcy", "rctd", "fl",
              "fga", "fgm", "xpa", "xpm"}
_DEF_STATS = {"tk", "sa", "in", "ff", "fr", "dtd"}


_SNAPS = {"run", "cmp", "inc", "int", "sack", "kneel", "pen"}


def _continuity(lg, ev, V):
    """Frame and possession continuity of one TOLD game (Fable's build review,
    the class 6.2 is): the next snap starts where the last event left the ball,
    with the right offence, down and distance; the view's own spots agree with
    the log; turnover banners and takeover spots match; a touchdown is followed
    by its try, a return touchdown by its score. Returns the issues."""
    bad = []
    if len(lg) != len(ev):
        return [("length", len(lg), len(ev))]
    exp, exp_kind = None, None
    for j, (le, e) in enumerate(zip(lg, ev)):
        k = e["k"]
        if le["k"] != k:
            bad.append(("kind", j, le["k"], k))
            continue
        if k == "2mw":
            continue
        if exp_kind is not None:
            if k in ("end", "ot") and exp_kind == "ko":
                pass
            elif k == "fin":
                exp_kind = None
            elif k != exp_kind:
                bad.append((f"expected {exp_kind}", j, k))
                exp_kind = None
            else:
                exp_kind = None
        if k in _SNAPS:
            o = e["o"]
            if exp is not None:
                if e.get("o") != exp["o"]:
                    bad.append(("offence", j))
                if exp.get("x") is not None and e.get("x0") != exp["x"]:
                    bad.append(("spot", j, exp["x"], e.get("x0")))
                if exp.get("d") is not None and e.get("d") != exp["d"]:
                    bad.append(("down", j, exp["d"], e.get("d")))
                if exp.get("t") is not None and e.get("t") != exp["t"]:
                    bad.append(("distance", j, exp["t"], e.get("t")))
                if exp.get("first") and e["t"] != min(10, e["y"]):
                    bad.append(("first-down distance", j, e["t"], e["y"]))
            if e.get("x0") != V._ax(o, e["y"]):
                bad.append(("x0 vs y", j))
            if e.get("fx") != V._ax(o, 0 if e["t"] >= e["y"] else e["y"] - e["t"]):
                bad.append(("first-down line", j))
            if le.get("td") or e.get("b") == "td":
                if not (le.get("td") and e.get("b") == "td"):
                    bad.append(("td flag vs banner", j))
                exp, exp_kind = None, "try"
            elif le.get("six"):
                if e.get("b") != "int":
                    bad.append(("pick-six banner", j))
                exp, exp_kind = None, "rtd"
            elif le.get("sf"):
                if e.get("b") != "sf":
                    bad.append(("safety banner", j))
                exp = None
            elif le.get("to") in ("int", "fum", "downs"):
                if e.get("b") != {"int": "int", "fum": "fum", "downs": "tod"}[le["to"]]:
                    bad.append(("turnover banner", j, le["to"], e.get("b")))
                if e.get("x1") != V._ax(1 - o, le["ty"]):
                    bad.append(("takeover spot", j))
                exp = {"o": 1 - o, "x": e.get("x1"), "d": 1, "first": True}
            elif k == "kneel":
                exp = {"o": o, "x": e.get("x1"), "d": e["d"] + 1, "t": e["t"]}
            elif k == "pen":
                yd = e["yd"]
                if le.get("fd"):
                    exp = {"o": o, "x": e.get("x1"), "d": 1, "first": True}
                else:
                    exp = {"o": o, "x": e.get("x1"), "d": e["d"], "t": max(1, e["t"] - yd) if yd > 0 else e["t"] - yd}
            else:
                if le.get("y2") is not None and e.get("x1") != V._ax(o, le["y2"]):
                    bad.append(("x1 vs y2", j))
                if le.get("fd"):
                    if not e.get("fd"):
                        bad.append(("first-down flag", j))
                    exp = {"o": o, "x": e.get("x1"), "d": 1, "first": True}
                else:
                    exp = {"o": o, "x": e.get("x1"), "d": e["d"] + 1, "t": e["t"] - e.get("yd", 0)}
        elif k == "punt":
            exp, exp_kind = ((None, "rtd") if le["res"] == "td"
                             else ({"o": 1 - e["o"], "x": e.get("x1"), "d": 1, "first": True}, None))
        elif k == "fg":
            exp = None if le["res"] == "good" else {"o": 1 - e["o"], "x": e.get("x1"), "d": 1, "first": True}
            if le["res"] == "good":
                exp_kind = "ko"
        elif k == "try":
            exp, exp_kind = None, "ko"
        elif k == "rtd":
            exp, exp_kind = None, "try"
        elif k == "ko":
            if le["res"] == "td":
                exp, exp_kind = None, "rtd"
            elif le["res"] == "onside_kept":
                exp = {"o": e["o"], "x": e.get("x1"), "d": 1, "first": True}
            else:
                exp = {"o": 1 - e["o"], "x": e.get("x1"), "d": 1, "first": True}
        elif k == "fk":
            exp = {"o": 1 - e["o"], "x": e.get("x1"), "d": 1, "first": True}
        elif k == "end":
            if e["q"] in (2, 4) or e["q"] >= 5:
                exp = None
                if e["q"] == 2:
                    exp_kind = "ko"
        elif k == "ot":
            exp, exp_kind = None, "ko"
    return bad


def _profiles(E, T):
    out = []
    for lh, la in ((1.0, 1.0), (1.12, 0.92), (0.9, 1.1)):
        h, a = E.league_profile(T, "GB"), E.league_profile(T, "CAR")
        h["level"], a["level"] = lh, la
        out.append((h, a))
    return out


def run(ck):
    import boardshare
    import nfl_deep_sim as E
    import nfl_viewer as V
    T = E.tables()
    mats = _profiles(E, T)

    # ==== the engine's log: passive, complete, legal =================================================
    modes = [{}, {"playoff": True}, {"neutral": True}, {"env_sd": 0.14}, {"form_sd": 0.0}]
    diff, empty, ends = [], 0, 0
    for mi, md in enumerate(modes):
        for hp, ap in mats:
            for s in range(200):
                seed = 7919 * s + 31 * mi
                g0 = E.play_game(hp, ap, random.Random(seed), T, **md)
                lg = []
                g1 = E.play_game(hp, ap, random.Random(seed), T, log=lg, **md)
                if g0 != g1:
                    diff.append((mi, s))
                if not lg:
                    empty += 1
                elif lg[0]["k"] != "ko" or lg[-1]["k"] != "fin":
                    ends += 1
    ck("the play log is passive: 200 seeds x 3 matchups x {regular, playoff, neutral, env_sd on, form_sd 0} play "
       "every box (points, quarters, every counter, ends, conversions, overtime, tie) identically with and without "
       "it, and every log opens on the kickoff and closes on the final",
       not diff and not empty and not ends, f"differ {diff[:5]} empty {empty} bad ends {ends}")

    players, roles = V.rosters(_stub_rows(), ("GB", "CAR"))
    names = {p["f"] for p in players}
    bad_box, bad_legal, bad_side, bad_def, bad_text, games = [], [], [], [], [], 0
    cont, real_downs = [], V._Downs()
    sacks = sack_gain = art_bad = 0
    dtot, team_named = [{}, {}], []
    scoring = {"rtd", "try"}
    for hp, ap in mats:
        for s in range(120):
            seed = V.game_seed(2026, 9, "GB", "CAR", s)
            lg = []
            g = E.play_game(hp, ap, random.Random(seed), T, log=lg)
            ev = V.tell(lg, players, roles, ("GB", "CAR"), seed)
            games += 1
            cont += [(s,) + c for c in _continuity(lg, ev, V)]
            real_downs.add(lg)
            for le, e in zip(lg, ev):
                gain = le["k"] == "sack" and le.get("yd", 0) > 0
                sacks += le["k"] == "sack"
                sack_gain += gain
                if gain != bool(e.get("art")) or (gain and "artifact" not in e["txt"]):
                    art_bad += 1
            tot = [{}, {}]
            per, clk, prev = 1, 900, [0, 0]
            for e in ev:
                for pi, st, n in e.get("cr", []):
                    side = players[pi]["t"]
                    tot[side][st] = tot[side].get(st, 0) + n
                    if st in _OFF_STATS and side != e.get("o"):
                        bad_side.append((e["k"], st))
                    if st in _DEF_STATS and side != 1 - e.get("o"):
                        bad_side.append((e["k"], st))
                    if st == "ktd" and side != 1 - e.get("o"):
                        bad_side.append((e["k"], st))
                if e["q"] != per:
                    per, clk = e["q"], e["c"]
                if e["c"] > clk:
                    bad_legal.append(("clock", e["k"], e["q"], e["c"], clk))
                clk = e["c"]
                if "d" in e and not (1 <= e["d"] <= 4 and 1 <= e["t"] <= e["y"] <= 99):
                    bad_legal.append(("down", e["k"], e["d"], e["t"], e["y"]))
                for key in ("x0", "x1", "fx", "xi"):
                    if key in e and not -10 <= e[key] <= 110:
                        bad_legal.append(("spot", key, e[key]))
                if list(e["s"]) != prev and not (e["k"] in scoring or e.get("b") in ("td", "sf", "fg")):
                    bad_legal.append(("score", e["k"], prev, e["s"]))
                prev = list(e["s"])
                # a defender only where the engine made the event certain
                if "df" in e:
                    ok = e["k"] in ("sack", "int") or (e["k"] in ("run", "cmp") and e.get("b") != "td"
                                                        and (e.get("b") in ("fum", "sf") or "out of bounds" not in e["txt"]))
                    if not ok:
                        bad_def.append((e["k"], e.get("b"), e["txt"][:60]))
                if e["k"] == "inc" and ("df" in e or any(c[1] in _DEF_STATS for c in e.get("cr", []))):
                    bad_def.append(("inc", e["txt"][:60]))
                t = e.get("txt") or ""
                low = t.lower()
                if (not t or "None" in t or "{" in t or "fair catch" in low or "touchback" in low
                        or "broken up" in low or "missed tackle" in low or "pressure" in low
                        or ("returned" in low and "touchdown" not in low)
                        or "Hurt" in t or "Sidelined" in t):
                    bad_text.append(t[:80])
                # every defensive role is filled on both sides of this roster, so a team code where a
                # defender's name belongs means the teller looked up a role that is not there
                if any(w + tm in t for tm in ("GB", "CAR") for w in (
                        "tackled by ", "sacked by ", "INTERCEPTED by ", "forced by ", "recovered by ",
                        "end zone by ", "TOUCHDOWN by ")):
                    team_named.append(t[:80])
            if prev != [g["h"]["pts"], g["a"]["pts"]]:
                bad_legal.append(("final", prev))
            for side in (0, 1):
                for st in ("tk", "ff", "fr"):
                    dtot[side][st] = dtot[side].get(st, 0) + tot[side].get(st, 0)
            for side, b in ((0, g["h"]), (1, g["a"])):
                for bk, st in _BOX.items():
                    if b[bk] != tot[side].get(st, 0):
                        bad_box.append((bk, b[bk], tot[side].get(st, 0)))
                if (tot[side].get("rc", 0), tot[side].get("rcy", 0), tot[side].get("tg", 0), tot[side].get("rctd", 0)) \
                        != (b["comp"], b["pass_yds"], b["pass_att"], b["td_pass"]):
                    bad_box.append(("receiving", side))
                if (tot[1 - side].get("in", 0), tot[1 - side].get("sa", 0)) != (b["int"], b["sacks"]):
                    bad_box.append(("defence", side))
                if tot[side].get("ktd", 0) + tot[side].get("dtd", 0) != b["td_ret"] or sum(b["q"]) != b["pts"]:
                    bad_box.append(("returns/quarters", side))
    ck(f"the log replays to a legal game ({games} games): the clock never runs backward inside a period, every snap "
       "is a real down and distance inside the field, the score moves only on a scoring play and ends at the box's",
       not bad_legal, str(bad_legal[:5]))
    ck("the names add up to the engine's box in every game: passing, rushing, receiving (catches = completions, "
       "targets = attempts), lost fumbles, kicking, the defence's sacks and interceptions, return touchdowns",
       not bad_box, str(bad_box[:5]))
    ck("every credited player is on the right side: the offence's numbers to the side with the ball, a tackle, sack, "
       "interception, forced fumble or recovery to the other side, a return touchdown to the returning side",
       not bad_side, str(bad_side[:5]))
    ck("a defender is named only on an event the engine made certain (a tackle that ends an in-bounds, non-scoring "
       "run or catch, a safety, a sack, an interception, a lost fumble) -- never on an incompletion (no pass "
       "breakups) and never on a touchdown or out of bounds",
       not bad_def, str(bad_def[:5]))
    ck(f"the defence is named at all ({games} games): each side is credited 20+ tackles a game and 0.1+ forced "
       "fumbles and recoveries (39.4-39.9 and 0.29 measured 2026-10-30; a safety's three tackles in 360 games cannot "
       "carry it), and no play names a TEAM where its tackler, sacker, interceptor, fumble forcer, recoverer or "
       "returner belongs (a teller that named none passed every other check here, Fable's round-2 probe)",
       all(dtot[sd].get("tk", 0) >= 20 * games and dtot[sd].get("ff", 0) >= 0.1 * games
           and dtot[sd].get("fr", 0) >= 0.1 * games for sd in (0, 1)) and not team_named,
       f"credits {dtot}; team named {team_named[:3]}")
    ck("the text invents nothing the engine does not play (no fair catches, touchbacks, return lengths, pressures, "
       "breakups or missed tackles) and never names a player Sleeper lists Out or on IR, however big his projection",
       not bad_text and not any("Hurt" in n or "Sidelined" in n for n in names), str(bad_text[:4]))
    ck(f"the told game is continuous ({games} games): every snap starts where the last event left the ball, with the "
       "right offence, down and distance (first and ten, or goal); the field's spots agree with the log; turnover "
       "banners and takeover spots match; a touchdown is followed by its try, a return touchdown by its score",
       not cont, str(cont[:5]))
    ck("every sack the engine scores as a GAIN is marked a fitted-table artifact on the screen and in its text, and "
       "nothing else is (it sat unmarked in the typical game of the first matchup the owner would open)",
       art_bad == 0 and sack_gain > 0, f"{art_bad} wrong of {sacks} sacks ({sack_gain} gains)")
    ck("the engine's sack-gain rate is pinned: at most 10% of its sacks net yards (7.9% measured 2026-10-29; the "
       "fourth-down refit should bring it near 2%) -- a refit that made it worse would pass every other check",
       sacks > 100 and sack_gain / sacks <= 0.10, f"{sack_gain} of {sacks}")
    tb_real = real_downs.table(games)
    sums_bad, rows_read = [], 0
    for side_ in ("home", "away"):
        for r in tb_real[side_]:
            # an empty down is {"down": d, "n": 0}; a counted one carries per_game and no "n" (the first version
            # skipped on a missing "n" and so read no row at all: its mutant was caught only by the synthetic check)
            if "per_game" not in r:
                continue
            rows_read += 1
            tot_ = sum((r.get(k_) or 0) for k_ in ("run_pct", "pass_pct", "punt_pct", "fg_pct"))
            if abs(tot_ - 100.0) > 0.2 or (r["down"] < 4 and ((r.get("fg_pct") or 0) or (r.get("punt_pct") or 0))):
                sums_bad.append((side_, r["down"], round(tot_, 2), r.get("fg_pct"), r.get("punt_pct")))
    ck("on real logs every down's calls add up to 100%: runs and dropbacks on 1st-3rd down (the kick the clock "
       "forces there is not a call: 110 of them in these 360 games), plus punts and field goals on 4th -- all eight "
       "rows read",
       rows_read == 8 and not sums_bad, f"{rows_read} rows read; {sums_bad}")

    # ==== the sack clamp: a gain on a sack stops short of the goal line ===============================
    raw = dict(T.raw["sack_yds"])
    old = T.sack_yds
    illegal = forced_gains = 0
    try:
        T.sack_yds = E._H({"30": 1})
        for s in range(150):
            lg = []
            E.play_game(mats[0][0], mats[0][1], random.Random(s), T, log=lg)
            illegal += sum(1 for e in lg if e.get("y") is not None and e["y"] <= 0)
            forced_gains += sum(1 for e in lg if e["k"] == "sack" and e.get("yd", 0) > 0)
    finally:
        T.sack_yds = old
    ck("a sack the engine scores as a gain (its EFFECTIVE yards: a penalty enforced on the play) stops short of the "
       "goal line, as the engine's pre-snap penalty does: every sack forced to +30 leaves no snap at or past the goal "
       "(3.7% of real-matchup games carried the ball past it and scored next snap, 2026-10-29)",
       illegal == 0 and forced_gains > 0 and T.raw["sack_yds"] == raw,
       f"{illegal} snaps at or past the goal line; {forced_gains} forced sack gains (723 on 2026-10-30)")

    # ==== the typical game's definition =================================================================
    ck("the typical game is measured on the betting coordinates and nothing else: margin, total, each side's net "
       "passing and rushing yards, turnovers and plays",
       V.DIMS == ("margin", "total", "pass_home", "pass_away", "rush_home", "rush_away", "to_home", "to_away",
                  "plays_home", "plays_away") and len(V.summary(E.play_game(*mats[0], random.Random(1), T))) == 10)
    base = (10, 240, 120, 110, 1, 1, 64, 62)
    sums = [(1, 40) + base, (2, 52) + base, (9, 45) + base, (-1, 45) + base, (0, 45) + base, (14, 60) + base,
            (8, 44) + base]
    i, centre, scale, side, _d = V.choose(sums)
    dist = [sum(((x - c) / q) ** 2 for x, c, q in zip(s_, centre, scale)) for s_ in sums]
    near_any = min(range(len(sums)), key=lambda j: dist[j])
    near_untied = min((j for j in range(len(sums)) if sums[j][0] != 0), key=lambda j: dist[j])
    ck("the typical game's winner is the side the MEDIAN margin favours: here the game nearest the centre is a tie "
       "and the nearest untied one a loss by a point; the chosen one is the nearest WIN",
       side == 1 and centre[0] == 2 and sums[near_any][0] == 0 and sums[near_untied][0] < 0 and i == 0,
       f"chose {i} {sums[i][:2]}; nearest {near_any}, nearest untied {near_untied}")
    pk = [(0, 40) + base, (-3, 40) + base, (3, 40) + base, (0, 41) + base, (6, 40) + base, (-6, 40) + base]
    i2, c2, _s2, side2, _d2 = V.choose(pk)
    pk2 = [(7, 40) + base, (0, 40) + base, (0, 40) + base, (-2, 52) + base, (-7, 40) + base]
    i3, c3_, _s3, side3, _d3 = V.choose(pk2)
    ck("a median margin of 0 is a pick'em: either side may win, ties never, equal distances keep the lower index, "
       "and a game decided by three or fewer comes before a nearer blowout (LV @ NYJ: a 7-point win under 'NYJ "
       "53.6%')",
       side2 == 0 and c2[0] == 0 and i2 == 1 and side3 == 0 and i3 == 3, f"{i2} side {side2}; {i3} side {side3}")
    rng = random.Random(42)
    rs = [tuple(rng.randint(-20, 20) if j == 0 else rng.randint(0, 300) for j in range(10)) for _ in range(800)]
    import statistics as st
    cols = list(zip(*rs))
    med = [st.median(c) for c in cols]
    sc = [st.pstdev(c) or 1.0 for c in cols]
    sg = (med[0] > 0) - (med[0] < 0)
    elig = [j for j, s_ in enumerate(rs) if s_[0] != 0 and (not sg or (s_[0] > 0) == (sg > 0))]
    brute = min(elig, key=lambda j: (0 if sg or abs(rs[j][0]) <= V._PICKEM_MARGIN else 1,
                                     sum(((x - c) / q) ** 2 for x, c, q in zip(rs[j], med, sc)), j))
    got = V.choose(rs)[0]
    code = ("import sys, json; sys.path.insert(0, %r); import nfl_viewer as V; "
            "print(V.choose([tuple(x) for x in json.load(sys.stdin)])[0])" % _ROOT)
    other = subprocess.run([sys.executable, "-c", code], input=json.dumps(rs), capture_output=True, text=True,
                           timeout=60).stdout.strip()
    ck("the choice is the brute-force nearest eligible game by medians and population SDs (a pick'em: a game "
       "decided by three or fewer first), and another process picks the same one",
       got == brute and other == str(got), f"{got} brute {brute} other {other!r}")

    # ==== the replay must be the game that was chosen ===================================================
    hp, ap = mats[1]
    sums_, downs, typ, others, ch = V.simulate_matchup(hp, ap, 2026, 9, T, n=300, k_random=4)
    import zlib
    ck("the typical game and the others are replayed from their seeds (crc32 of season|week|home|away|i, the same in "
       "every process) with the log on and reproduce the selection run's coordinates; the others are the first "
       "seeds, never the typical one",
       V.game_seed(2026, 9, "GB", "CAR", 7) == zlib.crc32(b"2026|9|GB|CAR|7")
       and V.summary(typ[2]) == sums_[typ[0]] and typ[0] == ch[0] and [o[0] for o in others] == [x for x in range(5)
                                                                                                   if x != ch[0]][:4]
       and all(V.summary(o[2]) == sums_[o[0]] for o in others), str((typ[0], [o[0] for o in others])))
    calls = {"n": 0}
    real_summary = V.summary

    def _drift(g):
        calls["n"] += 1
        s_ = real_summary(g)
        return s_ if calls["n"] <= 300 else (s_[0] + 1,) + s_[1:]
    raised = False
    with _patched((V, "summary", _drift)):
        try:
            V.simulate_matchup(hp, ap, 2026, 9, T, n=300, k_random=2)
        except RuntimeError:
            raised = True
    ck("a replay that does not reproduce the selection run's game is refused (the naming pass must never touch the "
       "engine's generator)", raised)
    lg = list(typ[1])
    snap = copy.deepcopy(lg)
    a1 = V.tell(lg, players, roles, ("GB", "CAR"), 1234)
    a2 = V.tell(lg, players, roles, ("GB", "CAR"), 1234)
    ck("telling a game is deterministic by its seed and never changes the log", a1 == a2 and lg == snap)

    # ==== the per-down table ==============================================================================
    D = V._Downs()
    D.add([{"k": "run", "o": 0, "d": 1, "t": 10, "yd": 4, "fd": 0},
           {"k": "cmp", "o": 0, "d": 2, "t": 6, "yd": 3, "fd": 0},
           {"k": "sack", "o": 0, "d": 3, "t": 3, "yd": -7},
           {"k": "punt", "o": 0, "d": 4, "t": 10},
           {"k": "run", "o": 0, "d": 1, "t": 10, "yd": 12, "fd": 1},
           {"k": "pen", "o": 0, "d": 1, "t": 10, "yd": -5},
           {"k": "int", "o": 1, "d": 3, "t": 8, "yd": 0, "to": "int", "ty": 50},
           {"k": "fg", "o": 0, "d": 2, "t": 7, "x": 1, "res": "good"}])
    tb = D.table(1)
    h1, h2, h3, h4 = tb["home"]
    ck("the per-down table counts calls, not penalties: success is 40% / 60% / all of the distance, a sack counts "
       "as a dropback, a punt is a fourth-down call, an interception never succeeds",
       h1["per_game"] == 2 and h1["run_pct"] == 100.0 and h1["success"] == 100.0 and h1["first"] == 50.0
       and h2["success"] == 0.0 and h3["sack_pct"] == 100.0 and h3["pass_pct"] == 100.0 and h4["punt_pct"] == 100.0
       and h4["call"] == "punt" and tb["away"][2]["int_pct"] == 100.0 and tb["away"][2]["success"] == 0.0
       and h1["run_med"] in (4, 12) and h2["per_game"] == 1 and not h2.get("fg_pct"), json.dumps(tb)[:300])

    # ==== flavour ==========================================================================================
    import nfl_matchup_data as MD
    vocab = {"side": MD.SIDE, "gap": MD.GAP, "kind": MD.KIND}
    n = 100
    store = {"schema": MD.SCHEMA, "n": n, "teams": ["GB"], "vocab": vocab,
             "cols": {"off": [0] * n, "kind": [MD.KIND.index("run")] * n, "db": [0] * n, "sg": [1] * n,
                      "rloc": [MD.SIDE.index("left")] * n, "rgap": [MD.GAP.index("end")] * n, "ploc": [0] * n}}
    with _patched((MD, "load_season", lambda season: store)):
        fl, nfl = V.flavor(2026)
    p_le = dict(fl["GB"]["run"])["left end"]
    with _patched((MD, "load_season", lambda season: None)):
        none = V.flavor(2026)
    ck("run direction and the shotgun follow the team's own mix shrunk toward the league's by 60 plays (a team that "
       "ran left end 100 times reads (100 + 60 x 0.112) / 160); no store, every team is the league",
       nfl == n and abs(p_le - (100 + 60 * 0.112) / 160) < 1e-12
       and abs(fl["GB"]["sg_run"] - (100 + 60 * 0.418) / 160) < 1e-12 and none == ({}, 0), str(p_le))

    # ==== the piece =========================================================================================
    with _sandbox():
        with _patched((V, "flavor", lambda season: ({}, 0))):
            piece = V.build_game(2026, 9, "GB", "CAR", time.time() + 86400, "Packers", "Panthers", by="test",
                                 rows=_stub_rows(), profs={"GB": mats[1][0], "CAR": mats[1][1]}, T=T, n=300)
        raw_p = pickle.dumps(piece, protocol=pickle.HIGHEST_PROTOCOL)
        g0 = json.loads(piece["games"][0])
        pv = piece["provenance"]
        ck("a piece is one game's head and its eleven games as JSON text (the route sends one), under PIECE_MAX, its "
           "first the typical game, stamped with what built it (tables and engine hashes, the kickoff era, the seed "
           "scheme, the profiles' source) and the board's deep figure from the board's own call",
           piece["schema"] == V.SCHEMA and len(piece["games"]) == V.K_RANDOM + 1 and len(raw_p) <= V.PIECE_MAX
           and piece["sims"][0]["kind"] == "typical" and g0["events"][0]["k"] == "ko"
           and all(k_ in pv for k_ in ("tables_sha", "engine_sha", "kickoff_era", "seed_scheme", "profiles"))
           and piece["board"]["p_home"] == E.simulate(mats[1][0], mats[1][1], n=__import__("nfl_game_sim").deep_n(),
                                                      seed=zlib.crc32(b"2026|9|GB|CAR"))["p_home"]
           and set(piece["board"]) >= {"p_home", "p_away", "exp_home", "exp_away"} and piece["board"]["n"] == __import__("nfl_game_sim").deep_n() == 600
           and not piece["names"]["missing"], f"{len(raw_p)} bytes; missing {piece['names']['missing']}")

        # ---- names on real Sleeper rows; the doubt rule; a club with no defensive names is ledgered
        fx = json.load(open(os.path.join(_ROOT, "research", "data", "fixtures", "sleeper_w8_gb_car.json")))
        real = (fx["offence"], fx["idp"], fx["k"])
        pl_r, ro_r = V.rosters(real, ("GB", "CAR"))
        core = [(s_, r_) for s_ in (0, 1) for r_ in ("qb", "car", "rec", "miss", "tkl", "sack", "int", "k")
                if ro_r[s_].get(r_) is None or ro_r[s_].get(r_) == []]
        qb_gb = pl_r[ro_r[0]["qb"]]["f"] if ro_r[0].get("qb") is not None else None
        pl_d, ro_d = V.rosters(real, ("GB", "CAR"), doubt={("GB", qb_gb)})
        import errlog
        notes_r = []
        with _patched((V, "flavor", lambda season: ({}, 0)),
                      (errlog, "note", lambda code, *a, **k: notes_r.append((code, k.get("msg"))))):
            p_noidp = V.build_game(2026, 9, "GB", "CAR", time.time() + 86400, rows=(fx["offence"], [], fx["k"]),
                                   profs={"GB": mats[1][0], "CAR": mats[1][1]}, T=T, n=200)
        ck("on Sleeper's real week-8 rows (a committed GB / CAR cut) every role is filled for both clubs -- quarterback, "
           "ball carriers, receivers, targets, tacklers, pass rushers, interceptors, kicker; a player the slate holds in "
           "doubt is never named; a club with no defensive names says so in words and is ledgered (NFLV-roles)",
           not core and qb_gb and all(p_["f"] != qb_gb for p_ in pl_d)
           and "GB: no tackler" in p_noidp["names"]["missing"]
           and any(c_ == "NFLV-roles" and "tkl" in (m_ or "") for c_, m_ in notes_r),
           str((core, qb_gb, p_noidp["names"]["missing"], notes_r[:3])))
        side_ok = piece["side"] == 0 or abs(piece["board"]["p_home"] - 0.5) < 0.03 \
            or (piece["board"]["p_home"] > 0.5) == (piece["side"] > 0)
        ck("the typical game's winner is the board's favourite unless the matchup is within three points of a coin "
           "flip (the median margin and the board's own call agree on the side)", side_ok,
           str((piece["side"], piece["board"])))

        # ---- the route's reader (the week, the season and the PC's state pinned)
        import nfl_game_sim
        ko = time.time() + 2 * 86400
        sched = [("GB", "CAR", ko, "pre", "Packers", "Panthers")]
        built = []
        boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("built in the web worker"))
        worker_built = None
        r400 = c202 = c404 = c1 = c2 = c3 = c4 = c5 = c6 = c_far = c_busy = None
        body, j1, j2, handed, fresh_builds, stale_builds, started_builds = "{}", {}, {}, None, None, None, None
        far_handed, busy_note, pc_on_builds, late_builds, gate_order = None, "", None, None, None
        flask = {}
        pin = ((nfl_game_sim, "_season", lambda: 2026), (nfl_game_sim, "current_week", lambda preseason=False, day=None: 9),
               (boardshare, "pc_status", lambda path=None: {"state": "off"}))
        try:
          with _patched(*pin, (V, "week_schedule", lambda season, week: sched),
                        (V, "_server_build", lambda season, week, g: built.append(g) or True),
                        (V, "build_game", boom), (V, "simulate_matchup", boom), (V, "build_week", boom)):
            r400 = V.serve(9, "GB", "XXX")[1], V.serve(99, "GB", "CAR")[1], V.serve(9, "GB", "GB")[1]
            body, c202 = V.serve(9, "GB", "CAR")
            handed = len(built)
            with _patched((V, "week_schedule", lambda season, week: [])):
                c404 = V.serve(9, "GB", "CAR")[1]
            built.clear()
            c_far = V.serve(11, "GB", "CAR")[1]               # current week 9 + 1 is the furthest built
            far_handed = len(built)
            piece["kickoff_ts"] = ko
            boardshare.put(V.piece_name(2026, 9, "GB", "CAR"), piece)
            built.clear()
            b1, c1 = V.serve(9, "GB", "CAR", 3)
            j1 = json.loads(b1)
            b2, c2 = V.serve(9, "GB", "CAR", 0, now=ko + 3600)
            j2 = json.loads(b2)
            c3 = V.serve(9, "GB", "CAR", 0, now=ko + V.SHOW_AFTER_KO_S + 60)[1]
            fresh_builds = len(built)
            # Sunday's inactives: a piece built 3 h before kickoff is rebuilt once kickoff is 60 min away
            pth = os.path.join(boardshare._DIR, V.piece_name(2026, 9, "GB", "CAR") + ".pkl")
            t_ago = ko - 3 * 3600 - 600                           # built 3 h 10 min before kickoff
            os.utime(pth, (t_ago, t_ago))
            V.serve(9, "GB", "CAR", 0, now=ko - 3 * 3600)        # 10 min old, 3 h out: fresh
            V.serve(9, "GB", "CAR", 0, now=ko - 80 * 60)         # outside the late window: fresh
            pre_late = len(built)
            t_ago = ko - 3 * 3600                                 # built before the inactives
            os.utime(pth, (t_ago, t_ago))
            V.serve(9, "GB", "CAR", 0, now=ko - 60 * 60)
            late_builds = len(built) - pre_late
            built.clear()
            # with the PC on, a piece just past REBUILD_S is the PC's to rebuild; past the PC's lag, ours
            t_old = time.time() - V.REBUILD_S - 200
            os.utime(pth, (t_old, t_old))
            with _patched((boardshare, "pc_status", lambda path=None: {"state": "on"})):
                V.serve(9, "GB", "CAR", 0)
                pc_on_builds = len(built)
                # past the old lag (two back-to-back 150-s passes and the slack, 1,020 s) but inside the real
                # one (passes 340-400 s apart, _pc_lag): still the PC's -- the 5,000-game review's item 9
                t_old = time.time() - V.REBUILD_S - 2 * V._PC_BUDGET_S - boardshare.PC_SLACK_S - 60
                os.utime(pth, (t_old, t_old))
                V.serve(9, "GB", "CAR", 0)
                pc_on_builds = (pc_on_builds, len(built))
                t_old = time.time() - V.REBUILD_S - V._pc_lag() - 60
                os.utime(pth, (t_old, t_old))
                V.serve(9, "GB", "CAR", 0)
                pc_on_builds = pc_on_builds + (len(built),)
            built.clear()
            t_old = time.time() - V.REBUILD_S - 7200
            os.utime(pth, (t_old, t_old))
            b4, c4 = V.serve(9, "GB", "CAR", 0)
            stale_builds = len(built)
            b5, c5 = V.serve(9, "GB", "CAR", 0, now=ko + 600)
            started_builds = len(built)
            # through Flask: the mimetype and the bodies the page reads
            import app as A
            cl = A.app.test_client()
            for key, url in (("bad", "/api/nfl/viewer?week=9&home=GB&away=XXX"),
                             ("ok", "/api/nfl/viewer?week=9&home=GB&away=CAR&k=2")):
                r = cl.get(url)
                flask[key] = (r.status_code, r.mimetype, r.get_json())
            bad = dict(piece, schema=V.SCHEMA + 1)
            boardshare.put(V.piece_name(2026, 9, "GB", "CAR"), bad)
            c6 = V.serve(9, "GB", "CAR", 0)[1]
          # one server build at a time across workers: the gate held elsewhere -> "follows", no build here
          boardshare.put(V.piece_name(2026, 9, "GB", "CAR"), {"schema": -1})
          with _patched(*pin, (V, "week_schedule", lambda season, week: sched),
                        (V, "build_isolated", lambda *a, **k: built.append("child"))):
              assert boardshare.claim(V._SERVER_GATE)
              try:
                  built.clear()
                  V._MEM.clear()
                  bb, c_busy = V.serve(9, "GB", "CAR", 0)
                  busy_note = json.loads(bb).get("note") or ""
                  busy_started = (len(built), dict(V._MEM))
              finally:
                  boardshare.release(V._SERVER_GATE)
          # the gate is claimed INSIDE HEAVY_BUILD: a wait behind another heavy build holds no claim, so its
          # 300 s takeover clock starts with the child (Fable round 2: claimed before the wait, a second worker
          # could take it over mid-build and start a second child)
          import deep_cache
          ran = []
          V._MEM.clear()
          with _patched((V, "build_isolated", lambda *a, **k: ran.append(V._gate_held()))):
              deep_cache.HEAVY_BUILD.acquire()
              try:
                  wait_started = V._server_build(2026, 9, sched[0])
                  time.sleep(0.3)
                  gate_in_wait = V._gate_held()
              finally:
                  deep_cache.HEAVY_BUILD.release()
              t_end = time.time() + 10
              while not ran and time.time() < t_end:
                  time.sleep(0.05)
              time.sleep(0.2)
          gate_order = (wait_started, gate_in_wait, ran, V._gate_held())
          V._MEM.clear()
        except AssertionError as e:          # something simulated inside the route
            worker_built = repr(e)
        ck("the route reads only: an unknown team or week is refused, a game off the schedule is 404, a missing piece "
           "of an upcoming game answers 202 and hands ONE build to the child path -- nothing simulates in the worker",
           worker_built is None and r400 == (400, 400, 400) and c202 == 202
           and json.loads(body).get("building") is True and handed == 1 and c404 == 404,
           str((worker_built, r400, c202, handed, c404)))
        ck("a published piece is served one game at a time (k picks it), a fresh one never rebuilt; through Flask the "
           "answer is JSON with the head and the one game, a bad team 400 JSON",
           worker_built is None and c1 == 200 and j1["head"]["k"] == 3 and j1["game"] == json.loads(piece["games"][3])
           and "games" not in j1["head"] and fresh_builds == 0
           and flask.get("ok", (0,))[0] == 200 and flask["ok"][1] == "application/json"
           and flask["ok"][2]["head"]["k"] == 2 and "events" in flask["ok"][2]["game"]
           and flask.get("bad", (0,))[0] == 400 and flask["bad"][1] == "application/json",
           str((c1, fresh_builds, {k_: v_[:2] for k_, v_ in flask.items()})))
        ck("after kickoff the piece is the PRE-GAME simulation: served marked started until kickoff + 4 h, never "
           "rebuilt, then refused; a stale piece of an upcoming game is served while its rebuild is handed off; a "
           "piece of another schema is a missing one",
           worker_built is None and c2 == 200 and j2["head"]["started"] is True and c3 == 410 and c4 == 200
           and stale_builds == 1 and c5 == 200 and started_builds == 1 and c6 == 202,
           str((c2, c3, c4, stale_builds, c5, started_builds, c6)))
        ck("a piece built before Sunday's inactives is rebuilt once kickoff is within 75 minutes (never before, never "
           "for a fresh one); with the PC on, a piece just past 12 h is the PC's to rebuild and the server waits the "
           "PC's real lag -- the next pass, two passes 340-400 s apart, the budget and the upload slack (1,670 s; the "
           "old 1,020 s read the passes as back to back) -- so a piece past the old lag is still the PC's; only this "
           "week's and next week's games are built",
           worker_built is None and late_builds == 1 and pre_late == 0 and pc_on_builds == (0, 0, 1) and V._pc_lag() == 2 * V._PASS_GAP_S + V._PC_BUDGET_S + boardshare.PC_SLACK_S == 1670
           and c_far == 404 and far_handed == 0, str((late_builds, pre_late, pc_on_builds, c_far, far_handed)))
        ck("one server build at a time across every worker: while another build holds the gate the route answers 202 "
           "'a simulation is running; this game follows' and starts nothing (three taps used to start three 60 MB "
           "children); the gate is claimed only once HEAVY_BUILD is held, held through the child, released after",
           worker_built is None and c_busy == 202 and "this game follows" in busy_note and busy_started == (0, {})
           and gate_order == (True, False, [True], False),
           str((c_busy, busy_note, busy_started if c_busy else None, gate_order)))

        # ---- builds are pre-game only, in a child, and never over PIECE_MAX
        soon = [("GB", "CAR", time.time() + 300, "pre", "Packers", "Panthers")]
        res = V.build_week(2026, 9, soon, by="test")
        ticks = {"n": 0}

        def _late(g, now=None):
            ticks["n"] += 1
            return ticks["n"] == 1
        notes = []
        import errlog
        real_note = errlog.note
        quiet = ((V, "flavor", lambda season: ({}, 0)), (V, "sleeper_rows", lambda season, week: _stub_rows()),
                 (nfl_game_sim, "_deep_profiles", lambda s, w: {"GB": mats[0][0], "CAR": mats[0][1]}),
                 (nfl_game_sim, "availability", lambda season, week, teams=None, hist=None: {}),
                 (V, "N_SIMS", 200), (errlog, "note", lambda code, *a, **k: notes.append(code)))
        with _patched(*quiet, (V, "upcoming", _late)):
            res2 = V.build_week(2026, 9, sched, by="test")
        with _patched(*quiet, (V, "PIECE_MAX", 1000)):
            res3 = V.build_week(2026, 9, sched, by="test")
        names_after = [n_ for n_, _m in boardshare.names(V.PREFIX) if n_.endswith("_CAR_GB")]
        ck("a game ten minutes or less from kickoff is never built, a build that runs past the kickoff publishes "
           "nothing (a pre-game piece only), and a piece over PIECE_MAX is refused and ledgered (NFLV-size)",
           res["failed"] == ["CAR@GB: started"] and not res["built"]
           and res2["failed"] == ["CAR@GB: kicked off during the build"] and not res2["built"]
           and not res3["built"] and "over PIECE_MAX" in " ".join(res3["failed"]) and "NFLV-size" in notes
           and errlog.note is real_note, str((res, res2, res3, notes, names_after)))

    src_serve = inspect.getsource(V.serve)
    src_srv = inspect.getsource(V._server_build)
    src_iso = inspect.getsource(V.build_isolated)
    app_src = open(os.path.join(_ROOT, "app.py"), encoding="utf-8").read()
    route = app_src[app_src.index('@app.route("/api/nfl/viewer")'):]
    route = route[:route.index("@app.route", 10)]
    body_route = route.split('"""')[-1]
    ck("the viewer is never simulated in a web worker: the route calls only nfl_viewer.serve, serve only hands a "
       "build to _server_build, which waits on build_isolated under HEAVY_BUILD -- deep_cache.run_child, a niced "
       "child whose result rides a private fd (a stray print cannot corrupt it) and whose stderr reaches the ledger",
       "nfl_viewer.serve(" in body_route
       and not any(x in body_route for x in ("build_game", "simulate_matchup", "play_game", "build_week", "build_isolated"))
       and not any(x in src_serve for x in ("build_game(", "simulate_matchup(", "play_game(", "build_week("))
       and "_server_build(" in src_serve and "HEAVY_BUILD" in src_srv and "build_isolated(" in src_srv
       and "deep_cache.run_child(" in src_iso and "os.nice(10)" in inspect.getsource(__import__("deep_cache").run_child)
       and "os.dup2(2, 1)" in inspect.getsource(__import__("deep_cache").run_child))

    # ==== the PC and the stores ============================================================================
    import nfl_preseason
    import nfl_game_sim as NG
    calls = []
    with _sandbox():
        with _patched((nfl_preseason, "is_preseason", lambda: False), (NG, "_season", lambda: 2026),
                      (NG, "current_week", lambda preseason=False, day=None: 9),
                      (V, "week_schedule", lambda season, week: []),
                      (V, "build_isolated", lambda *a, **k: calls.append((a, k)) or {"built": [], "failed": [], "left": []})):
            none_due = V.pc_build()
        up = [("GB", "CAR", time.time() + 86400, "pre", "Packers", "Panthers"),
              ("BUF", "BAL", time.time() + 86400, "pre", "Bills", "Ravens"),
              ("DAL", "ARI", time.time() - 60, "in", "Cowboys", "Cardinals")]
        boardshare.put(V.piece_name(2026, 9, "GB", "CAR"), {"schema": V.SCHEMA})
        due = V._due(2026, 9, up)
        t_old = time.time() - V.REBUILD_S - 60
        os.utime(os.path.join(boardshare._DIR, V.piece_name(2026, 9, "GB", "CAR") + ".pkl"), (t_old, t_old))
        due2 = V._due(2026, 9, up)
        with _patched((nfl_preseason, "is_preseason", lambda: False), (NG, "_season", lambda: 2026),
                      (NG, "current_week", lambda preseason=False, day=None: 9),
                      (V, "week_schedule", lambda season, week: up),
                      (V, "build_isolated", lambda *a, **k: calls.append((a, k)) or {"built": ["x"], "failed": [], "left": []})):
            some = V.pc_build()
    ck("the PC builds the week's upcoming games whose piece is missing or 12 h old, in the child with its pass "
       "budget, and answers something every pass (None reads as 'still building')",
       isinstance(none_due, dict) and none_due.get("skipped") and [g[0] for g in due] == ["BUF"]
       and [g[0] for g in due2] == ["GB", "BUF"] and some == {"built": ["x"], "failed": [], "left": []}
       and calls and calls[-1][1].get("by") == "PC" and calls[-1][1].get("budget_s") == V._PC_BUDGET_S,
       str((none_due, due, due2, some)))
    pc_src = open(os.path.join(_ROOT, "pc_worker.py"), encoding="utf-8").read()
    import artifacts
    import app as A
    sw = open(os.path.join(_ROOT, "static", "sw.js"), encoding="utf-8").read()
    html = open(os.path.join(_ROOT, "templates", "index.html"), encoding="utf-8").read()
    appjs = open(os.path.join(_ROOT, "static", "app.js"), encoding="utf-8").read()
    ck("wired: the PC's boards pass builds the pieces, boardshare keeps them a week (a game-day failure leaves no "
       "hole), the artifact schema moved, the page loads the script, the cache-buster and the service worker know it, "
       "the NFL card opens the viewer with its teams escaped, and the PC's upload takes a piece's name (no prefix "
       "list a future allowlist could drop it from)",
       '_add("nfl-viewer", lambda: __import__("nfl_viewer").pc_build())' in pc_src
       and "nflview_" in boardshare._KEEP_LONG and artifacts.SCHEMA == 8
       and '"nflview.js"' in inspect.getsource(A._asset_version) and '"/static/nflview.js"' in sw
       and '<script src="/static/nflview.js?v={{ v }}"></script>' in html
       and 'data-nv="open" data-nv-home="${escapeHtml(g.home)}" data-nv-away="${escapeHtml(g.away)}"' in appjs
       and artifacts.valid_name(V.piece_name(2026, 9, "GB", "CAR") + ".pkl")
       and "startswith(" not in inspect.getsource(__import__("pc_worker")._sync_kind))

    # ==== the page's script ==================================================================================
    js_path = os.path.join(_ROOT, "static", "nflview.js")
    js = open(js_path, encoding="utf-8").read()
    inline = [h for h in ("onclick=", "onchange=", "oninput=", "onload=", "onerror=") if h in js]
    node_ok, node_out = None, ""
    dom_ok, dom_out = None, ""
    if shutil.which("node"):
        chk = subprocess.run(["node", "--check", js_path], capture_output=True, text=True, timeout=30)
        evil = "<img src=x onerror=alert(1)>"
        head = {k_: v_ for k_, v_ in piece.items() if k_ != "games"}
        head = json.loads(json.dumps(head))
        head.update(k=0, started=True, home_name=evil, away_name=evil)
        head["players"] = [dict(p, n=evil, p=evil) for p in head["players"]]
        head["names"] = {"missing": [evil]}
        head["flavor"] = {"source": evil}
        gm = json.loads(piece["games"][0])
        for e in gm["events"]:
            e["txt"] = evil
        tmpd = tempfile.mkdtemp()
        data_path = os.path.join(tmpd, "data.json")
        with open(data_path, "w") as fh:
            json.dump({"head": head, "game": gm, "card": {"home": "GB", "away": "CAR", "home_name": evil,
                                                          "away_name": evil, "p_home_deep": 0.6}}, fh)
        harness = (
            "const vm = require('vm'); const fs = require('fs');"
            f"const src = fs.readFileSync({json.dumps(js_path)}, 'utf8');"
            f"const data = JSON.parse(fs.readFileSync({json.dumps(data_path)}, 'utf8'));"
            "const noop = () => {};"
            "const ctx = {document: {addEventListener: noop, getElementById: () => null,"
            " body: {classList: {add: noop, remove: noop}}}, performance: {now: () => 0},"
            " requestAnimationFrame: noop, cancelAnimationFrame: noop, setTimeout: noop, clearTimeout: noop,"
            " console, __data: data};"
            "vm.createContext(ctx);"
            "vm.runInContext(src + `;\n_NV.el = {innerHTML: '', querySelector: () => null, querySelectorAll: () => [],"
            " classList: {add() {}, remove() {}, contains() { return false; }}};"
            " _NV.g = __data.card; _NV.head = __data.head; _NV.ev = __data.game.events; _NV.i = _NV.ev.length - 1;"
            " _NV.box = __data.game.box; _nvRender();"
            " globalThis.__out = [_NV.el.innerHTML, _nvPlays(), _nvBox(), _nvDowns()];`, ctx);"
            "console.log(JSON.stringify(ctx.__out.map((x) => x.toLowerCase().includes('<img'))));")
        # the screen's own logic over a whole game: every event shown, its field plan
        # drawn at four moments, under a DOM stub that flags any NaN / undefined
        # coordinate or label (Fable: _nvPlan / _nvFrame / _nvShow never ran under a guard)
        clean = os.path.join(tmpd, "clean.json")
        with open(clean, "w") as fh:
            json.dump({"head": dict(json.loads(json.dumps({k_: v_ for k_, v_ in piece.items() if k_ != "games"})),
                                    k=0, started=False),
                       "game": json.loads(piece["games"][0]),
                       "card": {"home": "GB", "away": "CAR", "home_name": "Packers", "away_name": "Panthers",
                                "p_home_deep": 0.62, "exp_home_deep": 24.31, "exp_away_deep": 20.07}}, fh)
        dom = (
            "const vm = require('vm'); const fs = require('fs');"
            f"const src = fs.readFileSync({json.dumps(js_path)}, 'utf8');"
            f"const data = JSON.parse(fs.readFileSync({json.dumps(clean)}, 'utf8'));"
            "const noop = () => {}; const bad = []; const els = {};"
            "const badv = (v) => v === undefined || v === null || (typeof v === 'number' && !isFinite(v))"
            " || /NaN|undefined|Infinity/.test(String(v));"
            "function mk(id) { if (els[id]) return els[id]; const e = {id, attrs: {}, className: '',"
            " set textContent(v) { if (badv(v)) bad.push([id, 'text', String(v)]); this._t = v; }, get textContent() { return this._t; },"
            " set innerHTML(v) { if (/NaN|undefined/.test(String(v))) bad.push([id, 'html', String(v).slice(0, 80)]); this._h = v; },"
            " get innerHTML() { return this._h; }, value: 0,"
            " setAttribute(k, v) { if (badv(v)) bad.push([id, k, String(v)]); this.attrs[k] = v; },"
            " classList: {add: noop, remove: noop, toggle: noop, contains: () => false},"
            " querySelector(sel) { return mk(id + ' ' + sel); }, querySelectorAll() { return []; }};"
            " els[id] = e; return e; }"
            "const ctx = {document: {addEventListener: noop, getElementById: (id) => mk(id), createElement: () => mk('new'),"
            " body: {classList: {add: noop, remove: noop}, appendChild: noop}}, performance: {now: () => 0},"
            " requestAnimationFrame: noop, cancelAnimationFrame: noop, setTimeout: noop, clearTimeout: noop,"
            " console, __data: data, __bad: bad, __mk: mk};"
            "vm.createContext(ctx);"
            "vm.runInContext(src + `;\n_NV.el = __mk('nvOverlay'); _NV.g = __data.card; _NV.head = __data.head;"
            " _NV.ev = __data.game.events; _NV.box = __data.game.box; _nvRender();"
            " let n = 0; for (let j = 0; j < _NV.ev.length; j++) { _NV.i = j; _nvShow(j, false);"
            " const plan = _nvPlan(_NV.ev[j]); for (const t of [0, 0.3, 0.6, 1]) { _nvFrame(plan, t); n++; } }"
            " _nvBanner(null); globalThis.__n = n;`, ctx);"
            "console.log(JSON.stringify({frames: ctx.__n, events: data.game.events.length, bad: bad.slice(0, 5), n_bad: bad.length}));")
        try:
            r = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=60)
            node_out = (r.stdout + r.stderr)[-600:]
            node_ok = chk.returncode == 0 and r.returncode == 0 and r.stdout.strip() == "[false,false,false,false]"
            r2 = subprocess.run(["node", "-e", dom], capture_output=True, text=True, timeout=120)
            dom_out = (r2.stdout + r2.stderr)[-600:]
            try:
                dres = json.loads(r2.stdout.strip().splitlines()[-1])
            except Exception:
                dres = None
            dom_ok = (r2.returncode == 0 and dres is not None and dres["n_bad"] == 0
                      and dres["frames"] == 4 * dres["events"] and dres["events"] > 100)
        except Exception as e:          # node present but the harness broke: a failure, not a skip
            node_ok, node_out = False, repr(e)
            dom_ok, dom_out = False, repr(e)
        finally:
            shutil.rmtree(tmpd, ignore_errors=True)
    no_node = "" if shutil.which("node") else \
        "(skipped for a stated reason: node is not on this machine's path; CI's runner has it) "
    ck(no_node + "the viewer's script has no inline handlers and parses; under node, the real render functions given a piece "
       "whose every name, team and play text is an <img onerror> write none of it raw (the frame, the play list, "
       "the box score, the per-down table)",
       not inline and node_ok is not False, f"inline {inline}; node {node_out}")
    ck(no_node + "the screen's own logic runs a whole game clean: every event shown and its field plan drawn at four moments "
       "write no NaN, undefined or Infinity into a coordinate, a label, a line or the text, and nothing throws",
       dom_ok is not False, dom_out)
    nv_open = js[js.index("function nvOpen"):js.index("function nvClose")]
    ck("a viewer that is never built says so: the 202 poll gives up after four minutes with a reason (the server's "
       "build may have failed, it is in the error log) instead of 'simulating' forever, and every open starts the "
       "count afresh",
       "Not built after four minutes" in js and "_NV.polls = 0" in nv_open)


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
