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

# kind "top":  the N likeliest legs of the type, chosen by the combo maker's
#              own frontier (objective "safe" = likeliest slip at exactly N).
# kind "all":  the single best leg per game of the type at/above the floor —
#              "every game's X" — priced and fee'd like any combo leg.
# sides None = YES or NO, exactly as specified; HR is YES-only by request
# ("it keeps giving me no's" is how the sides control was born).
PRESETS = (
    {"id": "hits5", "label": "5 Hits", "emoji": "🖐️", "kind": "top",
     "types": ("Hit",), "n_legs": 5, "sides": None,
     "desc": "The 5 likeliest hit props on the board, YES or NO."},
    {"id": "hr3", "label": "3 Home Runs", "emoji": "💣", "kind": "top",
     "types": ("HR",), "n_legs": 3, "sides": frozenset(("yes",)),
     "desc": "The 3 likeliest home runs to HAPPEN. YES only, locked."},
    {"id": "ks80", "label": "Ks 80%+", "emoji": "🔥", "kind": "all",
     "types": ("Ks",), "floor": 0.80, "sides": None,
     "desc": "Every game's best pitcher strikeout prop at 80%+ likely."},
    {"id": "ml58", "label": "ML 58%+", "emoji": "💰", "kind": "all",
     "types": ("ML",), "floor": 0.58, "sides": None,
     "desc": "Every game's moneyline at 58%+ likely."},
    {"id": "rl80", "label": "Run lines 80%+", "emoji": "📏", "kind": "all",
     "types": ("Run line",), "floor": 0.80, "sides": None,
     "desc": "Every game's best run line at 80%+ likely."},
)


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
        include_live=False, types=set(spec["types"]), sides=spec["sides"])


def _build_all(games, spec):
    """One leg per game: the likeliest leg of the type at/above the floor.
    Legs are singles from different games, so the combined chance is the
    honest independent product — no correlation claim to overstate."""
    import baseball
    import combo_engine
    import kalshi_mlb
    floor = spec["floor"]
    groups, prob, cost, gross = [], 1.0, 1.0, 1.0
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
        ok = [c for c in cands if c.get("price_cents") and c["marg"] >= floor]
        if not ok:
            continue
        best = max(ok, key=lambda c: c["marg"])
        tk, close = (None, None)
        try:
            tk, close = kalshi_mlb.ticker_leg(idx, g.get("kalshi_suffix"),
                                              best.get("kref"))
        except Exception as e:
            errlog.note("PRESET-ticker", e)
        px = best["price_cents"]
        leg = {"type": best["type"], "label": best["label"],
               "side": best.get("side", "yes"),
               "prob_pct": round(best["marg"] * 100, 1),
               "model_pct": best.get("model_pct"),
               "sim_pct": (round(best["marg_model"] * 100, 1)
                           if best.get("marg_model") is not None else None),
               "market_cents": px,
               "market_payout_x": round(100.0 / px, 2),
               "ticker": tk, "close_time": close}
        groups.append({"matchup": g.get("matchup"), "legs": [leg]})
        prob *= best["marg"]
        cost *= combo_engine.leg_cost(px, net=True)
        gross *= 100.0 / px
    if not groups:
        return None
    net_x = round(1.0 / cost, 2) if cost > 0 else None
    return {"groups": groups, "n_games": len(groups),
            "combined_prob_pct": round(prob * 100, 1),
            "indep_prob_pct": round(prob * 100, 1),
            "fair_payout_x": round(1.0 / prob, 2) if prob > 0 else None,
            "kalshi_payout_x": round(gross, 2),
            "kalshi_payout_net_x": net_x,
            "ev_pct": (round((prob * net_x - 1) * 100, 1)
                       if net_x else None)}


def build_all(date, games, sig):
    """Build every preset against one pinned Kalshi book, log each slip."""
    import kalshi_mlb
    import sliplog
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
            key = None
            if item:
                item["objective"] = f"preset:{pid}"
                try:
                    key = sliplog.log_from_item(item, sport="mlb", date=date,
                                                tag=pid)
                except Exception as e:
                    errlog.note("PRESET-log", e, path=pid)
            out[pid] = {"label": spec["label"], "emoji": spec["emoji"],
                        "desc": spec["desc"], "item": item,
                        "logged": bool(key),
                        # An unlogged slip still shows; the note says why the
                        # ledger skipped it (thin slate, an unticketed leg).
                        "log_note": (None if key else
                                     "not in the ledger: needs 2+ legs, all "
                                     "with Kalshi tickets, pre-game")}
    return {"date": date, "sig": sig, "built_ts": int(time.time()),
            "presets": out}


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
            and cur.get("sig") == sig):
        return 0
    payload = build_all(date, games, sig)
    boardshare.put(NAME, payload)
    return 1
