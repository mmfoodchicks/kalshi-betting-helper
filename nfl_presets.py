"""Locked NFL slips -- presets.py's recipes on the week's football slate.

Same contract as the baseball and UFC recipes: the knobs are constants in
THIS file, every week's slip is built by the same rule, each build is
logged into the slip ledger (sport "nfl", tag "nfl_<id>") and graded off
Kalshi settlement, and the record hangs on the Football tab's own wall.
The owner's spec, near-verbatim: "5 likeliest anytime TDs, moneyline, run
line, 100+ yards receiving, 100+ rushing, 5+ receptions, 300+ passing
yards, then the 1.5x, 2x, 3x, 5x, 10, 100x, 200x ... its own hits section
and independent tabs like baseball for quick bets." Three families:

  kind "top"    the N likeliest legs of a type, chosen by the maker's own
                frontier (objective "safe"), a per-leg rule keeping only
                the rung the recipe is about (99.5+ rec yds, not 24.5+).
  kind "all"    every game's leg of a type nearest ABOVE the bar -- the
                owner's rule for the baseball scan recipes ("as close to
                80% as possible without going under"). NO legs count:
                "NO - LAC by over 10.5" is the 80% spread on a coin-flip
                game, and the pool carries every fade since sims5.
  kind "target" the maker's Optimal-for-my-x button, locked to a payout.

Rebuild rule: the week (or season type) or the rev changes, or the build
is older than _STALE_S -- NFL prices move all week, so a half-hourly
rebuild keeps the slip current without churning the ledger (it dedups by
leg set). Runs on the recorder's cadence, owner worker only, and ONLY off
sims that are already cached (the PC's upload, or a Build click's): a
16-game slate sim is ~25s of a one-core quota, which is a Build click's
cost when a person asked for it and an outage's cause when a background
loop pays it every half hour beside the MLB slate child. The tab's first
open kicks a forced build (app.api_nfl_presets), exactly as UFC's does.

Cost on cached sims, measured 2026-09-09 on the 16-game week-1 slate: a
frontier is ~3s, a three-floor optimal sweep ~13s; the seven rungs share
three frontiers through build_parlay's frontier_cache, and the seven
top/all recipes are one narrow-typed frontier each, so a full rebuild
is ~40s, once per half hour, on the recorder thread.
"""

import time

import errlog

NAME = "nfl_presets"          # boardshare key: one build, every worker serves it
TAG = "nfl_"                  # ledger tag prefix: "nfl_td5" etc.
REV = 2
_STALE_S = 1800
# The PC warms exactly this pool (pc_worker._task_boards: _slate_sims(wk,
# pre, 4000)), so the recipes ride its upload rather than paying their own.
_N = 4000


def _rung(line):
    """A per-leg rule keeping one rung of a player ladder: the recipe says
    100+ receiving yards, and Kalshi books that as the 99.5 strike beside
    a 24.5 and a 149.5 -- the maker would otherwise take the likeliest
    rung it can find (24.5+) and call it the recipe. Priced legs only: an
    unbooked rung is an invented line with no ticket to grade."""
    def ok(c):
        k = c.get("kref") or {}
        return k.get("line") == line and bool(c.get("price_cents"))
    return ok


_YES = frozenset(("yes",))

PRESETS = (
    {"id": "td5", "label": "5 Anytime TDs", "emoji": "🏈", "kind": "top",
     "types": ("TD",), "n_legs": 5, "sides": _YES, "leg_ok": _rung(0.5),
     "desc": "The 5 likeliest anytime touchdown scorers on the slate, YES "
             "only, at Kalshi's ask - the 0.5 rung, never a 2+ TD line "
             "dressed up as 'anytime'. Same-game stacks allowed: the joint "
             "comes off the game sim, so two scorers from one shootout "
             "carry their real correlation."},
    {"id": "ml58", "label": "ML 58%+", "emoji": "💰", "kind": "all",
     "types": ("ML",), "floor": 0.58, "sides": None, "pick": "floor",
     "desc": "Every game's moneyline closest to the 58% bar without going "
             "under - baseball's rule on the football slate. A coin-flip "
             "game with neither side above the bar sits out."},
    {"id": "sp80", "label": "Spreads 80%+", "emoji": "📏", "kind": "all",
     "types": ("Spread",), "floor": 0.80, "sides": None, "pick": "floor",
     "desc": "Every game's spread closest to the 80% bar without going "
             "under, YES or NO - 'NO, not by 10.5+' is the 80% line on a "
             "close game, and Kalshi books two dozen rungs a side. "
             "Nearest-above beats likeliest: same slot, better payout."},
    {"id": "rec100", "label": "100+ Rec Yds", "emoji": "🙌", "kind": "top",
     "types": ("Rec Yds",), "n_legs": 5, "sides": _YES, "leg_ok": _rung(99.5),
     "desc": "The 5 likeliest 100+ receiving-yard games on the slate, YES "
             "only, one rung (99.5) and nothing shallower."},
    {"id": "rush100", "label": "100+ Rush Yds", "emoji": "🏃", "kind": "top",
     "types": ("Rush Yds",), "n_legs": 5, "sides": _YES, "leg_ok": _rung(99.5),
     "desc": "The 5 likeliest 100+ rushing-yard games on the slate, YES "
             "only, at the 99.5 rung."},
    {"id": "rec5", "label": "5+ Receptions", "emoji": "🧤", "kind": "top",
     "types": ("Receptions",), "n_legs": 5, "sides": _YES, "leg_ok": _rung(4.5),
     "desc": "The 5 likeliest 5+ reception games on the slate, YES only, "
             "at the 4.5 rung."},
    {"id": "pass300", "label": "300+ Pass Yds", "emoji": "🎯", "kind": "top",
     "types": ("Pass Yds",), "n_legs": 5, "sides": _YES, "leg_ok": _rung(299.5),
     "desc": "The 5 likeliest 300+ passing-yard games on the slate, YES "
             "only, at the 299.5 rung. Thin most weeks - five quarterbacks "
             "clearing 300 is a rare slate, and a short slip is the recipe "
             "being honest."},
    # kind "target": the maker's ⚡ Optimal-for-my-× button, locked, knob
    # for knob with app.api_nfl_parlay's optimal mode: payout required,
    # legs off, "balanced", the per-leg floor swept by best_target. Seven
    # rungs, one tab, each logged and graded under its own tag. The payout
    # is what KALSHI PAYS for that exact combo -- the legs' asks multiplied,
    # with combo_engine.QUOTE_ROOM for the maker's cut -- and the slip is
    # one leg per game, because a maker prices a same-game stack's
    # correlation himself (see presets.py's rungs for the 133x lesson).
    {"id": "x15", "label": "Pays 1.5× (-200)", "emoji": "⚡", "kind": "target",
     "target_x": 1.5, "payout_basis": "market",
     "desc": "A combo whose Kalshi asks multiply to 1.5× (-200) and more, "
             "one leg per game, chosen for the highest true odds the sim "
             "can find at that price - mispriced legs are the edge. The "
             "bankroll-ladder rung."},
    {"id": "x2", "label": "Pays 2×", "emoji": "⚡", "kind": "target",
     "target_x": 2.0,
     "desc": "The likeliest slip whose Kalshi asks multiply to 2× and "
             "more (room for the maker's cut included), one leg per game, "
             "that isn't priced against you. Legs, floors and games are "
             "the optimizer's call."},
    {"id": "x3", "label": "Pays 3×", "emoji": "⚡", "kind": "target",
     "target_x": 3.0,
     "desc": "The likeliest slip whose Kalshi asks multiply to 3× and "
             "more, one leg per game, that isn't priced against you."},
    {"id": "x5", "label": "Pays 5×", "emoji": "⚡", "kind": "target",
     "target_x": 5.0,
     "desc": "The likeliest slip whose Kalshi asks multiply to 5× and "
             "more, one leg per game, that isn't priced against you."},
    {"id": "x10", "label": "Pays 10×", "emoji": "⚡", "kind": "target",
     "target_x": 10.0,
     "desc": "The likeliest slip whose Kalshi asks multiply to 10× and "
             "more, one leg per game, that isn't priced against you."},
    {"id": "x100", "label": "Pays 100×", "emoji": "⚡", "kind": "target",
     "target_x": 100.0,
     "desc": "The likeliest slip whose Kalshi asks multiply to 100× and "
             "more, one leg per game - about seven coin flips across seven "
             "games; ~1% shots by nature."},
    {"id": "x200", "label": "Pays 200×", "emoji": "⚡", "kind": "target",
     "target_x": 200.0,
     "desc": "The likeliest slip whose Kalshi asks multiply to 200× and "
             "more, one leg per game - the moonshot rung; empty on a thin "
             "slate is honest."},
)
TARGET_IDS = tuple(p["id"] for p in PRESETS if p["kind"] == "target")


def slate_sig(games):
    """Fingerprint of the pre-game slate: which games are still to be
    played. Prices are deliberately NOT in it -- they move all week, and
    the age rule handles that without a rebuild per tick. A game kicking
    off changes the sig, so Thursday's game leaves Friday's slips."""
    import hashlib
    import nfl_game_sim
    parts = sorted(str(g.get("pair") or g.get("suffix") or g.get("label"))
                   for g in games or [] if not nfl_game_sim.game_started(g))
    if not parts:
        return None
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _build_top(week, pre, spec, abort_cb=None):
    """The N likeliest legs of a type -- the maker's own machinery, locked.
    max_legs_per_game=n as in baseball: the recipe is 'the 5 likeliest',
    and two from one game are two of the five."""
    import nfl_game_sim
    # "The 5 likeliest" on a slate that books three: require the count and
    # walk it down (5, 4, 3, 2) rather than answer "nothing qualifies" --
    # 300+ passing at the 299.5 rung is booked for a handful of quarterbacks
    # most weeks, and a three-leg slip is the honest version of the recipe.
    # Never one leg: the ledger grades parlays (sliplog needs 2+).
    for n in range(spec["n_legs"], 1, -1):
        item = nfl_game_sim.build_parlay(
            week=week, preseason=pre, n_legs=n, target_pct=5, cap_pct=None,
            target_payout=0, n_sims=_N, max_legs_per_game=n, max_total_legs=n,
            legs_mode="require", payout_mode="off", conn="or", objective="safe",
            types=set(spec["types"]), sides=spec["sides"],
            leg_ok=spec.get("leg_ok"), abort_cb=abort_cb)
        if isinstance(item, dict) and item.get("error_hint"):
            return item
        if item:
            if n < spec["n_legs"]:
                item["short_slate"] = spec["n_legs"]
            return item
    return None


def _build_target(week, pre, spec, abort_cb=None, frontier_cache=None):
    """The ⚡ Optimal button as a locked recipe: mirrors app.api_nfl_parlay's
    optimal mode knob for knob so the tab and the button can never quietly
    disagree about what "optimal" means."""
    import combo_engine
    import nfl_game_sim
    target = min(float(spec["target_x"]), combo_engine.MAX_PAYOUT_X)
    room = combo_engine.QUOTE_ROOM
    bar = round(target * room, 2)

    def _b(floor):
        # A yield is not a failed floor: best_target swallows exceptions per
        # floor, so the supersede has to be re-raised past it.
        if abort_cb is not None and abort_cb():
            raise _Yield()
        return nfl_game_sim.build_parlay(
            week=week, preseason=pre, n_legs=4, target_pct=floor,
            cap_pct=None, target_payout=bar, n_sims=_N,
            max_legs_per_game=1, max_total_legs=30, legs_mode="off",
            payout_mode="require", conn="or", objective="balanced",
            types=None, payout_basis="market",
            abort_cb=abort_cb, frontier_cache=frontier_cache)
    try:
        item = combo_engine.best_target(_b)
    except _Yield:
        raise RuntimeError("superseded by a newer build")
    if item:
        item["target_payout_x"] = target
        item["target_market_x"] = bar
        item["quote_room_x"] = room
    return item


class _Yield(BaseException):
    """Carries a supersede past combo_engine.best_target's per-floor
    `except Exception` -- a BaseException so that net cannot catch it."""


def _build_all(games, spec, abort_cb=None):
    """One leg per GAME of the type at/above the floor -- the likeliest, or
    the one NEAREST the floor from above when pick="floor". Priced legs
    only, YES or NO as the spec allows; games are independent, so the
    product is the honest joint. Mirrors presets._build_all with the NFL
    pool (one moneyline and one spread ladder per game, so the unit IS the
    game)."""
    import combo_engine
    import kalshi_nfl
    import nfl_game_sim
    floor = spec["floor"]
    near_floor = spec.get("pick") == "floor"
    try:
        idx = kalshi_nfl.index() or {}
    except Exception as e:
        errlog.note("NFLP-index", e)
        idx = {}
    groups, prob, cost, gross = [], 1.0, 1.0, 1.0
    n_pool = n_legs = 0
    for g in games or []:
        if nfl_game_sim.game_started(g):
            continue
        if abort_cb is not None and abort_cb():
            raise RuntimeError("superseded by a newer build")
        cands = [dict(c) for c in g["cands"]
                 if c["type"] in spec["types"]
                 and (spec["sides"] is None
                      or c.get("side", "yes") in spec["sides"])]
        if not cands:
            continue
        nfl_game_sim.price_cands(cands, g["suffix"])
        priced = [c for c in cands if c.get("price_cents")]
        if not priced:
            continue
        n_pool += 1
        best = None
        for c in priced:
            if c["marg"] < floor:
                continue
            if (best is None
                    or (c["marg"] < best["marg"] if near_floor
                        else c["marg"] > best["marg"])):
                best = c
        if best is None:
            continue
        px = best["price_cents"]
        lc = combo_engine.leg_cost(px, net=True)
        if lc is None:
            continue                # unplaceable ask
        tk, close = (None, None)
        try:
            tk, close = kalshi_nfl.ticker_leg(idx, g["suffix"], best.get("kref"))
        except Exception as e:
            errlog.note("NFLP-ticker", e)
        leg = {"type": best["type"], "pick": best["label"],
               "side": best.get("side", "yes"), "kref": best.get("kref"),
               "prob_pct": round(best["marg"] * 100, 1),
               "model_pct": best.get("model_pct"),
               "sim_pct": (round(best["marg_model"] * 100, 1)
                           if best.get("marg_model") is not None else None),
               "market_cents": px, "market_payout_x": round(100.0 / px, 2),
               "fillable": best.get("fillable"),
               "ticker": tk, "close_time": close,
               "start_ts": nfl_game_sim._iso_ts(g.get("date"))}
        prob *= best["marg"]
        cost *= lc
        gross *= 100.0 / px
        n_legs += 1
        groups.append({"matchup": g.get("label"), "suffix": g.get("suffix"),
                       "size": 1, "same_game": False, "legs": [leg],
                       "joint_pct": round(best["marg"] * 100, 1)})
    if not groups:
        return None
    net_x = round(1.0 / cost, 2) if cost > 0 else None
    return {"groups": groups, "n_games": len(groups), "n_legs": n_legs,
            "n_pool": n_pool, "sport": "nfl",
            "combined_prob_pct": round(prob * 100, 1),
            "indep_prob_pct": round(prob * 100, 1),
            "fair_payout_x": round(1.0 / prob, 2) if prob > 0 else None,
            "kalshi_payout_x": round(gross, 2), "kalshi_payout_net_x": net_x,
            "ev_pct": round((prob * net_x - 1) * 100, 1) if net_x else None}


def build_all(week, pre, games, sig, abort_cb=None):
    """Every recipe against one cached slate sim and one market read. Pure
    compute; the ledger write lives in ensure_logged."""
    import nfl_game_sim
    out = {}
    # The seven rungs differ only in the payout they CHOOSE; the frontier
    # they choose from is identical per floor. One cache shared across them
    # turns 21 maker passes into 3 -- most of the rebuild's cost.
    fcache = {}
    for spec in PRESETS:
        pid = spec["id"]
        try:
            if spec["kind"] == "target":
                item = _build_target(week, pre, spec, abort_cb=abort_cb,
                                     frontier_cache=fcache)
            elif spec["kind"] == "top":
                item = _build_top(week, pre, spec, abort_cb=abort_cb)
            else:
                item = _build_all(games, spec, abort_cb=abort_cb)
        except RuntimeError as e:
            if "superseded" in str(e):
                raise
            errlog.note("NFLP-build", e, path=pid)
            item = None
        except Exception as e:
            errlog.note("NFLP-build", e, path=pid)
            item = None
        if isinstance(item, dict) and item.get("error_hint"):
            item = None
        if item:
            item["objective"] = f"preset:{TAG}{pid}"
            item["sport"] = "nfl"
        out[pid] = {"label": spec["label"], "emoji": spec["emoji"],
                    "desc": spec["desc"], "item": item,
                    "logged": False, "log_note": None}
    return {"season": nfl_game_sim._season(), "week": week,
            "preseason": bool(pre), "sig": sig, "rev": REV,
            "built_ts": int(time.time()), "presets": out}


def ensure_logged(payload):
    """File every slip into the ledger under its nfl_ tag. Idempotent (the
    ledger dedups by leg set). The slip's date is its first kickoff's ET
    day (sliplog.log_from_item off the legs' start_ts), so a Tuesday build
    for Sunday sits in Sunday's ledger. Returns True when any badge
    changed."""
    import sliplog
    changed = False
    for pid, p in (payload.get("presets") or {}).items():
        item = p.get("item")
        if not item:
            continue
        key = None
        try:
            key = sliplog.log_from_item(item, sport="nfl", tag=TAG + pid)
        except Exception as e:
            errlog.note("NFLP-log", e, path=pid)
        logged = bool(key) or bool(p.get("logged"))
        note = (None if logged else
                "not in the ledger: needs 2+ legs, all with Kalshi tickets")
        if logged != p.get("logged") or note != p.get("log_note"):
            p["logged"], p["log_note"] = logged, note
            changed = True
    return changed


def records():
    """{pid: record} for the NFL recipes, off the shared ledger tags."""
    import store
    out = {}
    for tag, rec in (store.preset_records() or {}).items():
        if tag.startswith(TAG):
            out[tag[len(TAG):]] = rec
    return out


def best_wins():
    import store
    out = {}
    for tag, w in (store.preset_best_wins() or {}).items():
        if tag.startswith(TAG):
            out[tag[len(TAG):]] = w
    return out


def best_today(payload, recs):
    """The crown, exactly as presets.best_today scores it."""
    import presets
    return presets.best_today(payload, recs)


def _week():
    """The week the recipes build for: nfl_track's cached answer when it
    has one (it costs ESPN calls and is refreshed there every 6h), else
    looked up and cached there so the two never disagree."""
    import nfl_game_sim
    import nfl_preseason
    import nfl_track
    pre = nfl_preseason.is_preseason()
    now = time.time()
    st = nfl_track._state
    if st.get("week") is None or now - (st.get("week_ts") or 0) > nfl_track._WEEK_EVERY_S:
        st["week"] = nfl_game_sim.current_week(pre)
        st["week_ts"] = now
    return st["week"], pre


def _yield_cb():
    """A user's combo build owns the CPU: a recipe rebuild in flight steps
    aside at its next boundary and the next tick retries."""
    import baseball
    try:
        return bool(baseball.combo_slot_holder(max_age=600))
    except Exception as e:
        errlog.note("NFLP-slot", e)
        return False


def cached_sims(week, pre):
    """The week's pool if it is already in the shared store, else None --
    the recipes never pay for the slate sim on the recorder's cadence (see
    the module note); a forced build (the tab's first open) does."""
    import boardshare
    import nfl_game_sim
    name = (f"nfl_parlay_sims5_{nfl_game_sim._season()}_w{week}"
            f"_{int(bool(pre))}_{_N}")
    disk, _age = boardshare.get(name, nfl_game_sim._SIMS_TTL)
    return disk


def tick(force=False):
    """Recorder-cadence entry point, August through February. Cheap when
    the slate and the rev match and the build is under _STALE_S old (one
    shared-store read and a hash)."""
    import boardshare
    import clock
    import nfl_game_sim
    import nfl_track
    d = clock.today_et()
    if not (d.month >= 8 or d.month <= 2):
        return 0
    if nfl_track.espn_blocked() and nfl_track._state.get("week") is None:
        return 0                    # the week lookup needs ESPN; parked
    week, pre = _week()
    games = cached_sims(week, pre)
    if games is None:
        if not force:
            return 0                # nobody has paid for the sims yet
        games = nfl_game_sim._slate_sims(week, pre, _N)
    sig = slate_sig(games)
    if not sig:
        return 0                    # nothing pre-game left this week
    cur, _age = boardshare.get(NAME, None)
    fresh = (cur and cur.get("sig") == sig and cur.get("rev") == REV
             and cur.get("week") == week and bool(cur.get("preseason")) == bool(pre)
             and time.time() - (cur.get("built_ts") or 0) < _STALE_S)
    if fresh and not force:
        if ensure_logged(cur):
            boardshare.put(NAME, cur)
        return 0
    if _yield_cb():
        return 0
    import jobs
    try:
        with jobs.timed("nfl-presets"):
            payload = build_all(week, pre, games, sig, abort_cb=_yield_cb)
    except RuntimeError as e:
        if "superseded" in str(e):
            return 0
        raise
    ensure_logged(payload)
    boardshare.put(NAME, payload)
    return 1
