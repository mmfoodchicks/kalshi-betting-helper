"""UFC pre-lock capture: freeze tonight's state before anyone knows the results.

What production would compute for the card, computed the same way and written
to disk with a UTC stamp, so the post-event work (the real DraftKings field,
the simulator's point distributions against DraftKings' scoring, the field
model) grades against inputs that provably existed before lock:

  dk_slate.json      dk.slate_for("ufc"): the draftables CSV, salaries, the
                     contests on the draft group; the biggest contests' detail
  card.json          ufc_data.card(): the bouts as ESPN lists them, with rounds
  ratings.json       every fighter's rating dict AS SIMULATED (after _apply_bio),
                     the raw rating, the bio, and the flags a reader needs
                     before trusting a distribution: fights, thin, defaulted,
                     debut, career record, DraftKings match
  board.json         ufc_sim's board computed here at production's n (15,000),
                     with ufc_prices.attach (Kalshi cents, fair win, edge,
                     confidence, fades_market) -- index-aligned dk_arr/won_arr
                     per fighter and per-sample end round / method
  market.json        the raw Kalshi KXUFCFIGHT map at capture time
  trust.json         model_trust's UFC weight, calibrate's UFC params, the
                     model weight cap ufc_prices would apply
  lineups.json       simulate.dfs_build on the slate CSV with this board: the
                     lineups and contest numbers the tab would show tonight,
                     built by the code as it stands (the cross-bout reblend
                     included), so what tonight's builds rested on is on record
  manifest.json      sha256 and bytes per file, captured_utc, the lock time
                     from the earliest contest start, hours_to_lock, pre_lock,
                     the commit and whether the tree was clean, every step's
                     error if one failed

    python3 -m research.ufc_capture [note]
"""
import datetime
import hashlib
import json
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data", "ufc")
N_SIMS = 15000          # ufc_sim.board()'s production n
DETAIL_TOP = 8


def _utc():
    return datetime.datetime.now(datetime.timezone.utc)


def _write(d, name, payload):
    path = os.path.join(d, name)
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True, default=str)
    raw = open(path, "rb").read()
    return {"file": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def _iso_ts(s):
    try:
        return datetime.datetime.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp()
    except Exception:
        return None


def capture(note="", n=N_SIMS, log=print):
    import dk
    import ufc_data
    import ufc_sim
    import ufc_prices
    import simulate
    from research import provenance
    t0 = _utc()
    stamp = t0.strftime("%Y%m%dT%H%M%SZ")
    d = os.path.join(DATA, stamp)
    os.makedirs(d, exist_ok=True)
    files, errors = {}, {}

    def step(name, fn):
        try:
            t = time.time()
            out = fn()
            log(f"[UFC-cap] {name}: ok ({time.time() - t:.0f}s)")
            return out
        except Exception as e:
            errors[name] = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}"
            log(f"[UFC-cap] {name}: FAILED {type(e).__name__}: {e}")
            return None

    # 1. DraftKings
    slate = step("dk_slate", lambda: dk.slate_for("ufc"))
    contests, details = [], {}
    if slate:
        dg = slate.get("draft_group_id")
        contests = step("dk_contests", lambda: dk.contests("ufc", dg)) or slate.get("contests") or []
        big = sorted(contests, key=lambda c: -float(c.get("prize_pool") or 0))[:DETAIL_TOP]
        for c in big:
            cid = c.get("id")
            det = step(f"dk_contest_detail_{cid}", lambda cid=cid: dk.contest_detail(cid))
            if det:
                details[str(cid)] = det
        files["dk_slate"] = _write(d, "dk_slate.json", {"slate": slate, "contests": contests, "detail": details})
    starts = [_iso_ts(c.get("starts") or c.get("start")) for c in contests] + [_iso_ts(v.get("starts")) for v in details.values()]
    starts = [s for s in starts if s]
    lock = min(starts) if starts else None

    # 2. the card and every fighter, as simulated
    card = step("card", ufc_data.card)
    if card:
        files["card"] = _write(d, "card.json", card)
    ratings, bouts = [], []
    if card and card.get("bouts"):
        for bt in card["bouts"]:
            def one(bt=bt):
                raw_a = ufc_data.fighter_rating(bt["a_id"], bt["a_name"])
                raw_b = ufc_data.fighter_rating(bt["b_id"], bt["b_name"])
                ra, rb = dict(raw_a), dict(raw_b)
                bio_err = None
                try:
                    ufc_sim._apply_bio(ra, rb)
                except Exception as e:
                    bio_err = f"{type(e).__name__}: {e}"
                res = ufc_sim.simulate_bout(ra, rb, rounds=bt.get("rounds", 3), n=n)
                res["weight"] = bt.get("weight")
                return {"bout": bt, "raw": {"a": raw_a, "b": raw_b}, "as_simulated": {"a": ra, "b": rb},
                        "bio": {"a": ufc_data.fighter_bio(bt["a_id"]), "b": ufc_data.fighter_bio(bt["b_id"])},
                        "bio_error": bio_err}, res
            got = step(f"bout_{bt['a_name']}_vs_{bt['b_name']}", one)
            if got:
                ratings.append(got[0])
                bouts.append(got[1])
    board = {"sport": "ufc", "event": (card or {}).get("event"), "date": (card or {}).get("date"), "n_sims": n, "bouts": bouts,
             "computed_here_utc": t0.strftime("%Y-%m-%d %H:%M:%S UTC"),
             "note": "computed by this capture with production's functions and n; the server's own board is an unseeded draw of the same model"}
    market = step("market", ufc_prices._market_map)
    if market is not None:
        files["market"] = _write(d, "market.json", market)
    if bouts:
        step("attach_prices", lambda: ufc_prices.attach(board))
        files["board"] = _write(d, "board.json", board)

    # 3. the flags a reader needs before trusting any fighter's distribution
    dk_names = {}
    if slate:
        try:
            for r in simulate.parse_dk_csv(slate["csv"]):
                dk_names[simulate._norm_name(r["name"]) if hasattr(simulate, "_norm_name") else r["name"].strip().lower()] = r
        except Exception as e:
            errors["dk_names"] = f"{type(e).__name__}: {e}"
    flags = []
    for rt, res in zip(ratings, bouts):
        for side in ("a", "b"):
            f = res[side]
            key = f["name"].strip().lower()
            dkr = dk_names.get(key)
            flags.append({"name": f["name"], "id": f["id"], "opponent": res["b" if side == "a" else "a"]["name"], "rounds": res["rounds"],
                          "fights": f["fights"], "thin": f["thin"], "defaulted": f["defaulted"], "debut": f["debut"],
                          "career_record": f.get("career_record"), "record": f.get("record"), "rating": f.get("rating"),
                          "win_pct": f["win_pct"], "kalshi_cents": f.get("kalshi_cents"), "fair_win": f.get("fair_win"), "edge": f.get("edge"),
                          "confidence": f.get("confidence"), "fades_market": f.get("fades_market"),
                          "proj": f["proj"], "floor": f["floor"], "median": f["median"], "ceil": f["ceil"],
                          "dk_salary": (int(dkr["salary"]) if dkr else None), "dk_matched_by_exact_name": bool(dkr),
                          "age": rt["as_simulated"][side].get("age"), "reach": rt["as_simulated"][side].get("reach"), "stance": rt["as_simulated"][side].get("stance")})
    files["ratings"] = _write(d, "ratings.json", {"fighters": ratings, "flags": flags,
                                                  "flag_definitions": {"thin": "fewer than 3 UFC box-score fights", "defaulted": "no UFC fights AND no pro career record: league-average rates",
                                                                       "debut": "no UFC fights but a pro record: finish rate and durability shrunk from it, every rate league-average",
                                                                       "dk_matched_by_exact_name": "the DraftKings draftable row found by exact lower-cased name; the DFS builder's own matching may differ"}})

    # 4. trust state
    def trust():
        out = {}
        try:
            import model_trust
            out["model_trust"] = model_trust.load()
            out["model_trust_weight_ufc"] = model_trust.weight("ufc")
        except Exception as e:
            out["model_trust_error"] = f"{type(e).__name__}: {e}"
        try:
            import calibrate
            out["calibrate_params_ufc"] = calibrate._params("ufc")
        except Exception as e:
            out["calibrate_error"] = f"{type(e).__name__}: {e}"
        out["model_weight_cap"] = ufc_prices._model_weight_cap()
        return out
    tr = step("trust", trust)
    if tr is not None:
        files["trust"] = _write(d, "trust.json", tr)

    # 5. what the tab would build tonight, on the code as it stands
    if slate and bouts:
        # apply_ufc reads ufc_sim.board(), which is non-blocking and empty in a
        # fresh process; hand it the board computed above (the same model, the
        # same n) so the build runs the tab's exact path, reblend included
        ufc_sim.board = lambda n=N_SIMS, _b=board: json.loads(json.dumps(_b))
        big = max(details.values(), key=lambda c: float(c.get("prize_pool") or 0)) if details else {}
        kw = {"contest": "gpp", "contest_size": (int(big.get("max_entries") or 0) or None), "entry_fee": float(big.get("entry_fee") or 1.0),
              "prize_pool": (float(big.get("prize_pool") or 0) or None), "first_prize": (float(big.get("first_prize") or 0) or None)}
        out = {"contest_used": {k: big.get(k) for k in ("id", "name", "entry_fee", "max_entries", "prize_pool", "first_prize", "places_paid", "starts")}}
        for label, extra in (("single_projection", {"n_lineups": 1, "objective": "projection"}),
                             ("single_leverage", {"n_lineups": 1, "objective": "leverage"}),
                             ("portfolio_10", {"n_lineups": 10, "objective": "projection"})):
            got = step(f"dfs_build_{label}", lambda extra=extra: simulate.dfs_build(slate["csv"], roster=6, cap=50000, sport="ufc", mode="classic", **kw, **extra))
            if got is not None:
                out[label] = got
        files["lineups"] = _write(d, "lineups.json", out)

    prov = provenance.stamp(worlds=n, model="ufc_sim.simulate_bout per bout + ufc_prices.attach + simulate.dfs_build, as served", seed="UNSEEDED (production's own draw is unseeded too)")
    man = {"captured_utc": t0.strftime("%Y-%m-%d %H:%M:%S UTC"), "capture_stamp": stamp, "event": (card or {}).get("event"), "date": (card or {}).get("date"),
           "draft_group_id": (slate or {}).get("draft_group_id"), "n_sims": n,
           "lock_utc": (datetime.datetime.fromtimestamp(lock, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if lock else None),
           "hours_to_lock": (round((lock - t0.timestamp()) / 3600.0, 2) if lock else None), "pre_lock": (bool(lock and t0.timestamp() < lock) if lock else None),
           "contests_detailed": sorted(details), "note": note, "files": files, "errors": errors, "provenance": prov,
           "finished_utc": _utc().strftime("%Y-%m-%d %H:%M:%S UTC")}
    with open(os.path.join(d, "manifest.json"), "w") as fh:
        json.dump(man, fh, indent=1, sort_keys=True, default=str)
    log(f"[UFC-cap] {d}: {len(files)} files, {len(errors)} errors, lock {man['lock_utc']}, {man['hours_to_lock']} h to lock, pre_lock {man['pre_lock']}")
    return man


if __name__ == "__main__":
    capture(note=" ".join(sys.argv[1:]))
