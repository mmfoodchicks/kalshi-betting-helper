"""Locked daily slips — the owner's standing bets, run by the house.

Five recipes the owner actually plays, hard-locked on purpose: the knobs are
constants in THIS file, not UI state, so every day's slip is built by the same
rule and the graded record means something. (Tunable presets would be the
combo maker with extra steps — and an untunable record is the point: these
run every day, get logged pre-game into the slip ledger, grade off Kalshi
settlement, and feed the same calibration everything else feeds.) The custom
combo maker is untouched; if you want knobs, that's the tool.

Rebuild rule: a preset re-runs when the DAY rolls or when the slate's lineup
picture changes — a lineup posting (projected -> confirmed), a starter
scratch, a probable change. Each rebuild logs its slip; the ledger dedups by
leg-set, so an unchanged rebuild is the same bet and a lineup-shifted one is
a new row. Runs on the recorder's cadence (~10 min), owner worker only.
"""

import hashlib
import time

import errlog

NAME = "mlb_presets"          # boardshare key: one build, every worker serves it
# Bump when a RECIPE changes: tick() rebuilds on a rev mismatch, so a deploy
# that edits a locked rule replaces today's slips immediately instead of
# waiting for the next lineup to post.
REV = 6

# The 5-Hits refinement, near-verbatim: "limited to 1 hit, UNLESS the model
# truly thinks a player can get 2 or it would be a good bet. It's only doing
# 'no 3+ hits' which is like 96% for most people." The first live day proved
# the point: likeliest-first over the whole hit ladder is won by deep-line NO
# padding -- high headline, no payout, negative EV after five slices of vig.
_HIT2_CONVICTION = 0.40    # "truly thinks": the model has 2+ hits at 40%+
_HIT2_EDGE_C = 5.0         # "a good bet": pre-blend model beats the ask by 5c+
                           # (edge mode's own bar for a real disagreement)
_HIT2_NO_EDGE_C = 8.0      # "a REALLY good bet": the stiffer bar a 2+ FADE
                           # must clear -- the top of edge mode's real-edge
                           # band. Probability alone can't qualify a fade
                           # (a likely fade of a deep line IS the padding).


def _hits5_leg_ok(c):
    """The hit ladder, as refined twice from live output: the 1+ line always
    qualifies, either side. A 2+ line needs to EARN its slot -- YES on
    conviction (model >= _HIT2_CONVICTION pre-blend) or a real edge
    (_HIT2_EDGE_C+); NO only as a really good bet (_HIT2_NO_EDGE_C+ on its
    own ask, never on probability alone). 'NO 3+' never: a 96% fade of a
    line almost nobody reaches is headline padding, not a bet."""
    line = (c.get("kref") or {}).get("line")
    if line == 1:
        return True
    p = (c.get("marg_model") if c.get("marg_model") is not None
         else c.get("marg"))
    if p is None:
        return False
    px = c.get("price_cents")
    edge = (p * 100.0 - px) if px else None
    if c.get("side", "yes") == "yes":
        return p >= _HIT2_CONVICTION or (edge is not None
                                         and edge >= _HIT2_EDGE_C)
    if line != 2:
        return False               # NO 3+ (and deeper): never
    return edge is not None and edge >= _HIT2_NO_EDGE_C

# kind "top":  the N likeliest legs of the type, chosen by the combo maker's
#              own frontier (objective "safe" = likeliest slip at exactly N).
# kind "all":  the single best leg per game of the type at/above the floor —
#              "every game's X" — priced and fee'd like any combo leg.
# sides None = YES or NO, exactly as specified; HR is YES-only by request
# ("it keeps giving me no's" is how the sides control was born).
PRESETS = (
    {"id": "hits5", "label": "5 Hits", "emoji": "🖐️", "kind": "top",
     "types": ("Hit",), "n_legs": 5, "sides": None, "leg_ok": _hits5_leg_ok,
     "desc": "The 5 likeliest 1+ hit props, YES or NO. A 2+ line must earn "
             "its slot: YES on conviction (model 40%+ or a 5¢+ edge), NO "
             "only as a really good bet (8¢+ edge). NO 3+ never."},
    {"id": "hr3", "label": "3 Home Runs", "emoji": "💣", "kind": "top",
     "types": ("HR",), "n_legs": 3, "sides": frozenset(("yes",)),
     "desc": "The 3 likeliest home runs to HAPPEN. YES only, locked."},
    {"id": "ks80", "label": "Ks 80%+", "emoji": "🔥", "kind": "all",
     "types": ("Ks",), "floor": 0.80, "sides": None,
     "desc": "Every PITCHER's best strikeout prop at 80%+ likely - both "
             "starters per game when both have priced ladders."},
    {"id": "ml58", "label": "ML 58%+", "emoji": "💰", "kind": "all",
     "types": ("ML",), "floor": 0.58, "sides": None,
     "desc": "Every game's moneyline at 58%+ likely."},
    {"id": "rl80", "label": "Run lines 80%+", "emoji": "📏", "kind": "all",
     "types": ("Run line",), "floor": 0.80, "sides": None,
     "desc": "Every game's best run line at 80%+ likely."},
    # pick "floor": walk the ladder to the line NEAREST the bar from above,
    # not the likeliest. The owner's spec, near-verbatim: "as close to 80% as
    # possible without it going under - I see some that are like 97% but I
    # know it can go lower." A 97% deep line and an 81% line fill the same
    # slot; the 81% one pays real money for the same recipe.
    {"id": "tot80", "label": "Totals 80%+", "emoji": "📊", "kind": "all",
     "types": ("Total",), "floor": 0.80, "sides": None, "pick": "floor",
     "desc": "Every game's total-runs line CLOSEST to the 80% bar without "
             "going under - whichever side lands nearest (Over 3.5 and "
             "Under 11.5 alike). Nearest-above beats likeliest: same slot, "
             "better payout."},
)


def best_today(payload, records):
    """Crown ONE recipe for the day. 'Best on paper' is the start, not the
    verdict: the score is the slip's fee-aware EV, adjusted by the recipe's
    OWN graded record once it has one (>=5 graded) — a recipe that keeps
    beating its claimed odds has earned a thumb on the scale, one that keeps
    missing them gets docked, capped at ±10 EV points so history colors the
    paper number without drowning it. Returns None until any recipe has a
    priced slip."""
    best = None
    for pid, p in (payload.get("presets") or {}).items():
        it = p.get("item")
        if not it or it.get("ev_pct") is None:
            continue
        score = float(it["ev_pct"])
        note = None
        r = (records or {}).get(pid) or {}
        graded = r.get("graded") or 0
        if graded >= 5:
            delta = (r.get("won", 0) - (r.get("expected") or 0.0)) / graded
            score += max(-10.0, min(10.0, 50.0 * delta))
            note = (f"record {r.get('won', 0)}-{graded - r.get('won', 0)} vs "
                    f"{r.get('expected')} expected")
        cand = {"id": pid, "label": p.get("label"), "emoji": p.get("emoji"),
                "score": round(score, 1), "ev_pct": it.get("ev_pct"),
                "prob_pct": it.get("combined_prob_pct"),
                "payout_x": it.get("kalshi_payout_net_x"),
                "record_note": note}
        if best is None or cand["score"] > best["score"]:
            best = cand
    return best


def slate_sig(games):
    """A fingerprint of the slate's lineup picture: pre-game games only, the
    probables and whether each lineup is posted. Changes exactly when the
    owner's rebuild rule says to re-run: a card posting, a scratch, a
    probable swap, a game going live/final (it leaves the fingerprint)."""
    parts = []
    for g in games or []:
        if (g.get("live") or {}).get("state") in ("Final", "Live"):
            continue
        cf = g.get("confirm") or {}
        parts.append(f'{g.get("game_pk")}:{g.get("home_sp_id")}'
                     f':{g.get("away_sp_id")}:{cf.get("home_lineup")}'
                     f':{cf.get("away_lineup")}:{cf.get("level")}')
    if not parts:
        return None
    return hashlib.sha1("|".join(sorted(parts)).encode()).hexdigest()[:16]


def _build_top(games, spec):
    """The N likeliest legs of a type — the maker's own machinery, locked."""
    import baseball
    n = spec["n_legs"]
    return baseball.build_mixed_parlay(
        games, n_legs=n, target_pct=5, cap_pct=None, target_payout=0,
        max_legs_per_game=n, max_total_legs=n,
        legs_mode="require", payout_mode="off", objective="safe",
        include_live=False, types=set(spec["types"]), sides=spec["sides"],
        leg_ok=spec.get("leg_ok"))


def _build_all(games, spec):
    """One leg per MARKET UNIT at/above the floor — likeliest line per unit,
    or the line NEAREST the floor from above when the spec says pick="floor".

    Not per GAME: a game has one moneyline and one run line but TWO starters,
    and "every game's pitchers" means both arms — the per-game version showed
    9 pitchers on an 11-game slate and made the other 13 look unassigned. The
    unit is the cand's `group` (K:<pitcher>, ML, Run line), so ML/RL still
    yield at most one leg per game while strikeouts yield one per pitcher.
    Legs multiply as independent — cross-game they are, and two opposing
    starters' K props face different lineups, so the product stays honest.
    `n_pool` counts the priced units scanned, so the tab can say "9 of 16
    cleared the bar" instead of leaving the gap mysterious."""
    import baseball
    import combo_engine
    import kalshi_mlb
    floor = spec["floor"]
    groups, prob, cost, gross = [], 1.0, 1.0, 1.0
    n_pool = n_legs = 0
    try:
        idx = kalshi_mlb.index()
    except Exception:
        idx = {}
    for g in games or []:
        if (g.get("live") or {}).get("state") in ("Final", "Live"):
            continue
        try:
            gs = baseball._game_sim(g)
        except Exception as e:
            errlog.note("PRESET-sim", e)
            continue
        cands = [dict(c) for c in gs["cands"]
                 if c["type"] in spec["types"]
                 and (spec["sides"] is None
                      or c.get("side", "yes") in spec["sides"])]
        if not cands:
            continue
        baseball._price_cands(cands, g.get("kalshi_suffix"))
        priced = [c for c in cands if c.get("price_cents")]
        n_pool += len({c.get("group") or c["label"] for c in priced})
        # pick "floor" inverts the per-unit choice: nearest ABOVE the bar
        # instead of likeliest. Everything below the floor is already gone,
        # so min() here can never go under it.
        near_floor = spec.get("pick") == "floor"
        by_unit = {}
        for c in priced:
            if c["marg"] < floor:
                continue
            k = c.get("group") or c["label"]
            if (k not in by_unit
                    or (c["marg"] < by_unit[k]["marg"] if near_floor
                        else c["marg"] > by_unit[k]["marg"])):
                by_unit[k] = c
        if not by_unit:
            continue
        glegs = []
        for best in sorted(by_unit.values(), key=lambda c: -c["marg"]):
            tk, close = (None, None)
            try:
                tk, close = kalshi_mlb.ticker_leg(
                    idx, g.get("kalshi_suffix"), best.get("kref"))
            except Exception as e:
                errlog.note("PRESET-ticker", e)
            px = best["price_cents"]
            # "pick" is the leg's display name everywhere a slip is rendered
            # (mlb_sim._mixed_item convention) -- the first live build showed
            # five "undefined"s because this said "label".
            glegs.append({"type": best["type"], "pick": best["label"],
                          "side": best.get("side", "yes"),
                          "prob_pct": round(best["marg"] * 100, 1),
                          "model_pct": best.get("model_pct"),
                          "sim_pct": (round(best["marg_model"] * 100, 1)
                                      if best.get("marg_model") is not None
                                      else None),
                          "market_cents": px,
                          "market_payout_x": round(100.0 / px, 2),
                          "ticker": tk, "close_time": close})
            prob *= best["marg"]
            cost *= combo_engine.leg_cost(px, net=True)
            gross *= 100.0 / px
            n_legs += 1
        groups.append({"matchup": g.get("matchup"), "legs": glegs})
    if not groups:
        return None
    net_x = round(1.0 / cost, 2) if cost > 0 else None
    return {"groups": groups, "n_games": len(groups),
            "n_legs": n_legs, "n_pool": n_pool,
            "combined_prob_pct": round(prob * 100, 1),
            "indep_prob_pct": round(prob * 100, 1),
            "fair_payout_x": round(1.0 / prob, 2) if prob > 0 else None,
            "kalshi_payout_x": round(gross, 2),
            "kalshi_payout_net_x": net_x,
            "ev_pct": (round((prob * net_x - 1) * 100, 1)
                       if net_x else None)}


def build_all(date, games, sig):
    """Build every preset against one pinned Kalshi book. PURE COMPUTE — the
    slip ledger is the SERVER's book of record, so filing lives in
    ensure_logged(); keeping this side-effect-free is what lets the PC build
    the identical payload and upload it without ever growing a ledger of
    its own."""
    import kalshi_mlb
    out = {}
    with kalshi_mlb.pinned():
        for spec in PRESETS:
            pid = spec["id"]
            try:
                item = (_build_top if spec["kind"] == "top"
                        else _build_all)(games, spec)
            except Exception as e:
                errlog.note("PRESET-build", e, path=pid)
                item = None
            if item:
                item["objective"] = f"preset:{pid}"
            out[pid] = {"label": spec["label"], "emoji": spec["emoji"],
                        "desc": spec["desc"], "item": item,
                        "logged": False, "log_note": None}
    return {"date": date, "sig": sig, "rev": REV,
            "built_ts": int(time.time()), "presets": out}


def ensure_logged(payload):
    """File every slip in the payload into the slip ledger. Idempotent — the
    ledger dedups by leg set — and safe on every tick, which is exactly how a
    PC-built payload (uploaded with logged=False) gets its slips recorded in
    the one place a ledger exists. Returns True when any flag changed, so the
    caller knows to republish the payload with its honest badges."""
    import sliplog
    changed = False
    for pid, p in (payload.get("presets") or {}).items():
        item = p.get("item")
        if not item:
            continue
        key = None
        try:
            key = sliplog.log_from_item(item, sport="mlb",
                                        date=payload.get("date"), tag=pid)
        except Exception as e:
            errlog.note("PRESET-log", e, path=pid)
        logged = bool(key) or bool(p.get("logged"))
        # An unlogged slip still shows; the note says why the ledger
        # skipped it (thin slate, an unticketed leg).
        note = (None if logged else
                "not in the ledger: needs 2+ legs, all with Kalshi "
                "tickets, pre-game")
        if logged != p.get("logged") or note != p.get("log_note"):
            p["logged"], p["log_note"] = logged, note
            changed = True
    return changed


def pc_build():
    """PC edition: build and PUBLISH, never log — the ledger lives in the
    server's DB, and the server's tick() files an adopted payload's slips on
    its next pass. Riding the boards store means the server adopts by
    freshness and still self-computes whenever the PC is off: the PC can only
    add speed, exactly like every other board."""
    import boardshare
    import clock
    import baseball
    date = clock.today_et().isoformat()
    games = baseball.analyze_slate(date, date[:4])
    sig = slate_sig(games)
    if not games or not sig:
        return None
    payload = build_all(date, games, sig)
    boardshare.put(NAME, payload)
    return payload


def tick(force=False):
    """Recorder-cadence entry point: rebuild when the day rolls or the slate's
    lineup picture changes. Cheap when nothing changed (one cached-slate read
    and a hash); a real rebuild rides the warmed sims and the pinned book."""
    import boardshare
    import clock
    import baseball
    date = clock.today_et().isoformat()
    try:
        games = baseball.analyze_slate(date, date[:4], cached_only=True)
    except Exception as e:
        errlog.note("PRESET-slate", e)
        return 0
    if games is None:
        return 0                    # board cold; the warmer gets there first
    sig = slate_sig(games)
    if not sig:
        return 0                    # nothing pre-game left today
    cur, _age = boardshare.get(NAME, None)
    if (not force and cur and cur.get("date") == date
            and cur.get("sig") == sig and cur.get("rev") == REV):
        # Fresh — self-built OR adopted from the PC. Either way the slips
        # belong in the ledger: a PC payload lands with logged=False and gets
        # filed here, then republished so the badges read true.
        if ensure_logged(cur):
            boardshare.put(NAME, cur)
        return 0
    # Debounce lineup-post storms: sigs can change minutes apart all
    # afternoon, and each rebuild is real CPU on a shared core the health
    # probe also lives on. The next tick catches whatever this one skips.
    if (not force and cur and cur.get("rev") == REV
            and time.time() - (cur.get("built_ts") or 0) < 300):
        return 0
    # A USER's combo build owns the CPU outright -- six recipe builds
    # stacking on top of it is exactly the concurrency that starves the
    # health probe into an instance restart. The next tick retries.
    try:
        if baseball.combo_slot_holder(max_age=600):
            return 0
    except Exception as e:
        errlog.note("PRESET-slot", e)
    payload = build_all(date, games, sig)
    ensure_logged(payload)
    boardshare.put(NAME, payload)
    return 1
