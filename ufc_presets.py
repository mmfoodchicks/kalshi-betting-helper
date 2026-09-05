"""Locked UFC slips -- presets.py's recipes, modelled for the fight card.

Same contract as the baseball presets: the knobs are constants in THIS
file, every card's slip is built by the same rule, each build is logged
into the slip ledger (sport "ufc", tag "ufc_<id>") and graded off Kalshi
settlement, and the record hangs on the UFC tab's own wall. YES legs only
throughout (see ufc_combo). Two families:

  kind "top"    the N likeliest legs of a type, one per bout, chosen by the
                maker's own frontier (objective "safe").
  kind "all"    every bout's leg of a type nearest ABOVE the bar -- the
                owner's rule for the baseball scan recipes ("as close to 80%
                as possible without going under").
  kind "target" the maker's Optimal-for-my-x button, locked to a payout.

Rebuild rule: the card (event + bouts) or the rev changes, or the build is
older than _STALE_S -- UFC prices move for a week, so a half-hourly rebuild
keeps the slip current without churning the ledger (it dedups by leg set).
Runs on the recorder's cadence, owner worker only, and costs well under a
second: the bout sims are on the board already and a card is a dozen bouts.
"""

import hashlib
import time

import errlog

NAME = "ufc_presets"          # boardshare key: one build, every worker serves it
TAG = "ufc_"                  # ledger tag prefix: "ufc_fav5" etc.
REV = 1
_STALE_S = 1800

PRESETS = (
    {"id": "fav5", "label": "5 Favorites", "emoji": "🥊", "kind": "top",
     "types": ("UFC ML",), "n_legs": 5,
     "desc": "The 5 likeliest fight winners on the card, one per bout - "
             "the fair win% (model blended with the book by what it has "
             "earned), priced at Kalshi's ask."},
    {"id": "fin3", "label": "3 Finishes", "emoji": "💥", "kind": "top",
     "types": ("Rounds",), "n_legs": 3,
     "desc": "The 3 likeliest 'fight ends before round N' legs, one per "
             "bout - the finishes the sim is surest of, at whichever rung "
             "Kalshi books."},
    {"id": "ml65", "label": "Winners 65%+", "emoji": "💰", "kind": "all",
     "types": ("UFC ML",), "floor": 0.65, "pick": "floor",
     "desc": "Every bout's winner closest to the 65% bar without going "
             "under. Nearest-above beats likeliest: same slot, better "
             "payout. A bout with no fighter above the bar sits out."},
    {"id": "rd60", "label": "Early finish 60%+", "emoji": "⏱️", "kind": "all",
     "types": ("Rounds",), "floor": 0.60, "pick": "floor",
     "desc": "Every bout's 'ends before round N' rung closest to the 60% "
             "bar without going under - the latest rung that still clears "
             "it, which is the one that pays."},
    {"id": "x2", "label": "Pays 2×", "emoji": "⚡", "kind": "target",
     "target_x": 2.0,
     "desc": "The likeliest slip that pays 2× and isn't priced against "
             "you. Legs, floors and bouts are the optimizer's call."},
    {"id": "x3", "label": "Pays 3×", "emoji": "⚡", "kind": "target",
     "target_x": 3.0,
     "desc": "The likeliest slip that pays 3× and isn't priced against "
             "you."},
    {"id": "x5", "label": "Pays 5×", "emoji": "⚡", "kind": "target",
     "target_x": 5.0,
     "desc": "The likeliest slip that pays 5× and isn't priced against "
             "you."},
    {"id": "x10", "label": "Pays 10×", "emoji": "⚡", "kind": "target",
     "target_x": 10.0,
     "desc": "The likeliest slip that pays 10× and isn't priced against "
             "you - expect same-fight stacks (a winner and the round it "
             "ends in) doing the heavy lifting."},
)
TARGET_IDS = tuple(p["id"] for p in PRESETS if p["kind"] == "target")


def card_sig(board):
    """Fingerprint of the card: event, date and the bout list. Prices are
    deliberately NOT in it -- they move all week, and the age rule below
    handles that without a rebuild per tick."""
    parts = [str(board.get("event")), str(board.get("date"))]
    parts += sorted(f"{bt['a']['id']}_{bt['b']['id']}" for bt in board.get("bouts") or [])
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _build_top(board, mk, spec, abort_cb=None):
    import ufc_combo
    n = spec["n_legs"]
    return ufc_combo.build_parlay(
        n_legs=n, target_pct=2, cap_pct=None, target_payout=0,
        max_legs_per_bout=1, max_total_legs=n, legs_mode="require",
        payout_mode="off", objective="safe", types=set(spec["types"]),
        board=board, mk=mk, abort_cb=abort_cb)


def _build_target(board, mk, spec, abort_cb=None):
    """The ⚡ Optimal button as a locked recipe, knob for knob with the
    endpoint's optimal mode: payout required, legs off, "balanced", the
    per-leg floor swept by combo_engine.best_target."""
    import combo_engine
    import ufc_combo
    target = min(float(spec["target_x"]), combo_engine.MAX_PAYOUT_X)

    def _b(floor):
        if abort_cb is not None and abort_cb():
            raise _Yield()
        return ufc_combo.build_parlay(
            n_legs=4, target_pct=floor, cap_pct=None, target_payout=target,
            max_legs_per_bout=30, max_total_legs=30, legs_mode="off",
            payout_mode="require", objective="balanced", types=None,
            board=board, mk=mk, abort_cb=abort_cb)
    try:
        item = combo_engine.best_target(_b)
    except _Yield:
        raise RuntimeError("superseded by a newer build")
    if item:
        item["target_payout_x"] = target
    return item


class _Yield(BaseException):
    """Carries a supersede past best_target's per-floor `except Exception`."""


def _build_all(board, mk, spec, abort_cb=None):
    """One leg per bout of the type at/above the floor: the likeliest, or
    the one NEAREST the floor from above when pick="floor". Priced legs
    only; independent across bouts, so the product is the honest joint."""
    import combo_engine
    import ufc_combo
    floor = spec["floor"]
    near_floor = spec.get("pick") == "floor"
    groups, prob, cost, gross = [], 1.0, 1.0, 1.0
    n_pool = n_legs = 0
    for bt in board.get("bouts") or []:
        if abort_cb is not None and abort_cb():
            raise RuntimeError("superseded by a newer build")
        cands, _n = ufc_combo.bout_cands(bt, mk)
        cands = [c for c in cands if c["type"] in spec["types"] and c.get("price_cents")]
        if not cands:
            continue
        n_pool += 1
        best = None
        for c in cands:
            if c["marg"] < floor:
                continue
            if (best is None
                    or (c["marg"] < best["marg"] if near_floor else c["marg"] > best["marg"])):
                best = c
        if best is None:
            continue
        px = best["price_cents"]
        leg = {"type": best["type"], "pick": best["label"], "side": "yes",
               "kref": best.get("kref"),
               "prob_pct": round(best["marg"] * 100, 1),
               "model_pct": best.get("model_pct"),
               "sim_pct": (round(best["marg_model"] * 100, 1)
                           if best.get("marg_model") is not None else None),
               "market_cents": px, "market_payout_x": round(100.0 / px, 2),
               "fillable": best.get("fillable"),
               "ticker": best.get("ticker"), "close_time": best.get("close_time")}
        prob *= best["marg"]
        cost *= combo_engine.leg_cost(px, net=True)
        gross *= 100.0 / px
        n_legs += 1
        groups.append({"matchup": f"{bt['a']['name']} vs {bt['b']['name']}",
                       "size": 1, "same_game": False, "legs": [leg],
                       "joint_pct": round(best["marg"] * 100, 1)})
    if not groups:
        return None
    net_x = round(1.0 / cost, 2) if cost > 0 else None
    return {"groups": groups, "n_games": len(groups), "n_legs": n_legs,
            "n_pool": n_pool, "sport": "ufc",
            "combined_prob_pct": round(prob * 100, 1),
            "indep_prob_pct": round(prob * 100, 1),
            "fair_payout_x": round(1.0 / prob, 2) if prob > 0 else None,
            "kalshi_payout_x": round(gross, 2), "kalshi_payout_net_x": net_x,
            "ev_pct": round((prob * net_x - 1) * 100, 1) if net_x else None}


def build_all(board, mk, abort_cb=None):
    """Every recipe against one board and one market read. Pure compute; the
    ledger write lives in ensure_logged."""
    out = {}
    for spec in PRESETS:
        pid = spec["id"]
        try:
            if spec["kind"] == "target":
                item = _build_target(board, mk, spec, abort_cb=abort_cb)
            elif spec["kind"] == "top":
                item = _build_top(board, mk, spec, abort_cb=abort_cb)
            else:
                item = _build_all(board, mk, spec, abort_cb=abort_cb)
        except RuntimeError as e:
            if "superseded" in str(e):
                raise
            errlog.note("UFCP-build", e, path=pid)
            item = None
        except Exception as e:
            errlog.note("UFCP-build", e, path=pid)
            item = None
        if isinstance(item, dict) and item.get("error_hint"):
            item = None
        if item:
            item["objective"] = f"preset:{TAG}{pid}"
        out[pid] = {"label": spec["label"], "emoji": spec["emoji"],
                    "desc": spec["desc"], "item": item,
                    "logged": False, "log_note": None}
    return {"event": board.get("event"), "date": board.get("date"),
            "sig": card_sig(board), "rev": REV, "built_ts": int(time.time()),
            "presets": out}


def ensure_logged(payload):
    """File every slip into the ledger under its ufc_ tag. Idempotent (the
    ledger dedups by leg set). Returns True when any badge changed."""
    import sliplog
    changed = False
    for pid, p in (payload.get("presets") or {}).items():
        item = p.get("item")
        if not item:
            continue
        key = None
        try:
            key = sliplog.log_from_item(item, sport="ufc",
                                        date=payload.get("date"), tag=TAG + pid)
        except Exception as e:
            errlog.note("UFCP-log", e, path=pid)
        logged = bool(key) or bool(p.get("logged"))
        note = (None if logged else
                "not in the ledger: needs 2+ legs, all with Kalshi tickets")
        if logged != p.get("logged") or note != p.get("log_note"):
            p["logged"], p["log_note"] = logged, note
            changed = True
    return changed


def records():
    """{pid: record} for the UFC recipes, off the shared ledger tags."""
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


def tick(force=False):
    """Recorder-cadence entry point. Cheap when the card and the rev match
    and the build is under _STALE_S old (one cached-board read and a hash)."""
    import boardshare
    import ufc_combo
    board = ufc_combo._board()
    if not board:
        return 0                    # no card, or the board is still building
    sig = card_sig(board)
    cur, _age = boardshare.get(NAME, None)
    fresh = (cur and cur.get("sig") == sig and cur.get("rev") == REV
             and time.time() - (cur.get("built_ts") or 0) < _STALE_S)
    if fresh and not force:
        if ensure_logged(cur):
            boardshare.put(NAME, cur)
        return 0
    import jobs
    try:
        with jobs.timed("ufc-presets"):
            payload = build_all(board, ufc_combo.markets())
    except RuntimeError as e:
        if "superseded" in str(e):
            return 0
        raise
    ensure_logged(payload)
    boardshare.put(NAME, payload)
    return 1
