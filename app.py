"""Kalshi crypto betting helper -- Flask backend.

Endpoints
  GET  /                      -> the live web UI
  GET  /api/coins            -> supported coins
  GET  /api/quote            -> live spot + signal for ad-hoc params (no save)
  POST /api/markets          -> create/track a market (saves a model snapshot)
  GET  /api/markets          -> list tracked markets with live signals; auto-resolves
  DELETE /api/markets/<id>   -> stop tracking a market
  GET  /api/stats            -> running accuracy + Brier score

This is a decision-support tool. The odds are model estimates, not guarantees.
"""

import os
import time
import threading

from flask import Flask, jsonify, request, render_template, Response

import prices
import odds
import store
import kalshi
import baseball
import tiers

app = Flask(__name__)
# Don't sort JSON keys: it's wasted work and crashes on any dict with mixed
# key types (e.g. integer prop lines alongside string keys).
app.json.sort_keys = False
store.init_db()

# Optional password protection (recommended when exposing it over a tunnel).
# Set APP_PASSWORD (and optionally APP_USER) in the environment to turn it on.
APP_USER = os.environ.get("APP_USER", "kalshi")
APP_PASSWORD = os.environ.get("APP_PASSWORD")


@app.before_request
def _auth():
    if not APP_PASSWORD:
        return
    a = request.authorization
    if not a or a.username != APP_USER or a.password != APP_PASSWORD:
        return Response("Login required", 401,
                        {"WWW-Authenticate": 'Basic realm="Kalshi Helper"'})


def _tier():
    """The subscription tier for this request. Gating is OFF by default, so this
    returns the unlimited 'owner' tier until TIERS_ENFORCED is set."""
    return tiers.resolve(request.cookies.get("tier"), request.cookies.get("owner_key"))


def _locked(feature):
    """If the current tier can't use `feature`, return a 402 JSON response to
    return from the endpoint; otherwise None."""
    tier = _tier()
    if tiers.has_feature(tier, feature):
        return None
    need = tiers.feature_tier(feature)
    return jsonify({"error": "upgrade_required", "feature": feature,
                    "required_tier": need, "current_tier": tier,
                    "message": f"{feature.replace('_', ' ').title()} needs the "
                               f"{tiers.TIERS[need]['label']} tier."}), 402


@app.route("/api/tiers")
def api_tiers():
    return jsonify(tiers.public(_tier()))

# Start the background Kalshi recorder exactly once, on the first request. This
# works the same under the dev server, gunicorn, or any host (the __main__ block
# is not run by gunicorn, so we can't rely on it).
_rec_lock = threading.Lock()
_rec_started = False


def _ensure_recorder():
    global _rec_started
    if _rec_started:
        return
    with _rec_lock:
        if _rec_started:
            return
        _rec_started = True
        try:
            import recorder
            recorder.start_background()
        except Exception:
            pass


@app.before_request
def _bootstrap_recorder():
    _ensure_recorder()


def _minutes_to_close(close_time):
    return max(0.0, (close_time - time.time()) / 60.0)


def _signal_for(coin, threshold, direction, close_time, yes_price_cents):
    spot = prices.get_spot(coin)
    candles = prices.get_candles(coin, granularity=60)
    mins = _minutes_to_close(close_time)
    sig = odds.compute_signal(spot, candles, threshold, direction, mins, yes_price_cents)
    return sig


def _auto_resolve(market):
    """Resolve a market if its window has closed and it isn't resolved yet."""
    if market["resolved"]:
        return market
    if time.time() < market["close_time"]:
        return market
    try:
        price = prices.get_price_at(market["coin"], market["close_time"])
    except Exception:
        return market
    return store.resolve_market(market["id"], price) or market


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/sw.js")
def service_worker():
    # Served from root so the service worker's scope covers the whole app.
    return app.send_static_file("sw.js"), 200, {"Content-Type": "application/javascript"}


@app.route("/api/coins")
def api_coins():
    return jsonify(sorted(prices.SUPPORTED_COINS.keys()))


@app.route("/api/quote")
def api_quote():
    """Ad-hoc signal without saving — for the live preview as you type."""
    try:
        coin = request.args.get("coin", "BTC").upper()
        threshold = float(request.args["threshold"])
        direction = request.args.get("direction", "above")
        close_time = float(request.args["close_time"])
        yp = request.args.get("yes_price_cents")
        yes_price = float(yp) if yp not in (None, "", "null") else None
    except (KeyError, ValueError) as e:
        return jsonify({"error": f"bad params: {e}"}), 400
    try:
        sig = _signal_for(coin, threshold, direction, close_time, yes_price)
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    sig["coin"] = coin
    sig["close_time"] = close_time
    return jsonify(sig)


@app.route("/api/markets", methods=["POST"])
def api_create_market():
    data = request.get_json(force=True, silent=True) or {}
    try:
        coin = str(data["coin"]).upper()
        threshold = float(data["threshold"])
        direction = data.get("direction", "above")
        close_time = float(data["close_time"])
        yp = data.get("yes_price_cents")
        yes_price = float(yp) if yp not in (None, "", "null") else None
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({"error": f"bad params: {e}"}), 400
    if direction not in ("above", "below"):
        return jsonify({"error": "direction must be 'above' or 'below'"}), 400

    ticker = data.get("kalshi_ticker") or None
    side = data.get("position_side")
    side = side.upper() if side in ("yes", "no", "YES", "NO") else None
    ec = data.get("entry_cost_cents")
    entry_cost = float(ec) if ec not in (None, "", "null") else None

    try:
        sig = _signal_for(coin, threshold, direction, close_time, yes_price)
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    market_id = store.add_market(
        coin, threshold, direction, close_time, yes_price,
        snap_prob_yes=sig["prob_yes"],
        snap_recommendation=sig["recommendation"],
        snap_spot=sig["spot"],
        kalshi_ticker=ticker, position_side=side, entry_cost_cents=entry_cost,
    )
    return jsonify({"id": market_id}), 201


@app.route("/api/markets/<int:market_id>/position", methods=["POST"])
def api_set_position(market_id):
    """Record (or clear) a held position so the app can advise when to sell."""
    data = request.get_json(force=True, silent=True) or {}
    side = data.get("position_side")
    side = side if side in ("YES", "NO", "yes", "no") else None
    ec = data.get("entry_cost_cents")
    try:
        entry_cost = float(ec) if ec not in (None, "", "null") else None
    except (ValueError, TypeError):
        return jsonify({"error": "bad entry_cost_cents"}), 400
    store.set_position(market_id, side, entry_cost)
    return jsonify({"ok": True})


@app.route("/api/markets")
def api_list_markets():
    out = []
    for m in store.list_markets():
        m = _auto_resolve(m)
        item = dict(m)
        if not m["resolved"]:
            try:
                sig = _signal_for(m["coin"], m["threshold"], m["direction"],
                                  m["close_time"], m["yes_price_cents"])
                item["signal"] = sig
            except Exception as e:
                item["signal_error"] = str(e)
                sig = None

            # Live Kalshi bid/ask if this market came from the scanner.
            live = None
            if m.get("kalshi_ticker"):
                try:
                    live = kalshi.get_market(m["kalshi_ticker"])
                    item["kalshi_live"] = live
                except Exception:
                    pass

            # Sell guidance for a held position.
            if sig and m.get("position_side") and m.get("entry_cost_cents") is not None:
                item["position"] = odds.sell_guidance(
                    m["position_side"], m["entry_cost_cents"],
                    sig["fair_yes_cents"], sig["fair_no_cents"],
                    yes_bid=(live or {}).get("yes_bid"),
                    no_bid=(live or {}).get("no_bid"),
                    yes_ask=(live or {}).get("yes_ask"),
                    no_ask=(live or {}).get("no_ask"),
                    minutes_to_close=sig.get("minutes_to_close"),
                )
        out.append(item)
    return jsonify(out)


@app.route("/api/markets/<int:market_id>", methods=["DELETE"])
def api_delete_market(market_id):
    store.delete_market(market_id)
    return jsonify({"ok": True})


@app.route("/api/kalshi/meta")
def api_kalshi_meta():
    return jsonify({"coins": kalshi.SCANNABLE_COINS, "timeframes": kalshi.TIMEFRAMES})


@app.route("/api/kalshi/scan")
def api_kalshi_scan():
    """Pull live open Kalshi contracts for a coin+timeframe and run the model
    on each, returning them ranked by edge (best opportunities first)."""
    coin = request.args.get("coin", "BTC").upper()
    timeframe = request.args.get("timeframe", "hourly")
    try:
        markets = kalshi.get_open_markets(coin, timeframe)
    except Exception as e:
        return jsonify({"error": f"kalshi fetch failed: {e}"}), 502
    if not markets:
        return jsonify({"coin": coin, "timeframe": timeframe, "markets": []})

    try:
        spot = prices.get_spot(coin)
        candles = prices.get_candles(coin, granularity=60)
    except Exception as e:
        return jsonify({"error": f"price feed failed: {e}"}), 502

    enriched = []
    for m in markets:
        mins = _minutes_to_close(m["close_time"]) if m["close_time"] else 0.0
        sig = odds.kalshi_signal(spot, candles, m, mins)
        item = dict(m)
        item["minutes_to_close"] = round(mins, 2)
        item["signal"] = sig
        # Best available edge on either side, for ranking.
        edges = [e for e in (sig["edge_yes_cents"], sig["edge_no_cents"]) if e is not None]
        item["best_edge"] = max(edges) if edges else None
        enriched.append(item)

    enriched.sort(key=lambda x: (x["best_edge"] is None, -(x["best_edge"] or 0)))

    # Volatility edge: what move is the strike ladder pricing vs realized? (Pro+)
    vol = None
    tier = _tier()
    if markets and tiers.has_feature(tier, "vol_edge"):
        close = max((m["close_time"] for m in markets if m["close_time"]), default=None)
        mins = _minutes_to_close(close) if close else 0.0
        vol = odds.vol_edge(spot, candles, markets, mins)
        # Cross-check against Deribit's DVOL (the sharp options market, BTC/ETH).
        if vol and tiers.has_feature(tier, "deribit"):
            import deribit
            dvol = deribit.get_dvol(coin)
            if dvol:
                vol["deribit_dvol_pct"] = dvol
                ki = vol.get("implied_annual_pct")
                src = "Kalshi-implied" if ki else "recent realized"
                if not ki:
                    ki = vol.get("realized_annual_pct")
                if ki:
                    r = ki / dvol
                    vol["deribit_ratio"] = round(r, 2)
                    if r >= 1.15:
                        vol["deribit_note"] = (f"{src} vol ({ki}%) is RICHER than Deribit ({dvol}%) "
                                               f"— favorites/near-money look cheap on Kalshi.")
                    elif r <= 0.87:
                        vol["deribit_note"] = (f"{src} vol ({ki}%) is CHEAPER than Deribit ({dvol}%) "
                                               f"— the wings/longshots look cheap on Kalshi.")
                    else:
                        vol["deribit_note"] = f"{src} vol ({ki}%) is in line with Deribit ({dvol}%)."

    return jsonify({"coin": coin, "timeframe": timeframe, "spot": round(spot, 2),
                    "markets": enriched, "vol": vol})


@app.route("/api/commodities/meta")
def api_commodities_meta():
    import commodities
    return jsonify({k: v["label"] for k, v in commodities.COMMODITIES.items()})


@app.route("/api/commodities/scan")
def api_commodities_scan():
    """Model Kalshi commodity price markets like crypto (GBM on daily prices)."""
    import commodities
    key = request.args.get("key", "gold")
    cfg = commodities.COMMODITIES.get(key)
    if not cfg:
        return jsonify({"error": "unknown commodity"}), 400
    try:
        spot = commodities.get_spot(key)
        candles = commodities.get_candles(key)
    except Exception as e:
        return jsonify({"error": f"price feed failed: {e}"}), 502
    markets = []
    for st in cfg["series"]:
        try:
            markets += kalshi.markets_for_series(st)
        except Exception:
            pass
    enriched = []
    now = time.time()
    for m in markets:
        if not m.get("yes_ask"):
            continue
        days = max(0.0, (m["close_time"] - now) / 86400.0) if m["close_time"] else 0.0
        sig = odds.kalshi_signal(spot, candles, m, days)  # day-based units
        item = dict(m)
        item["days_to_close"] = round(days, 1)
        item["signal"] = sig
        edges = [e for e in (sig["edge_yes_cents"], sig["edge_no_cents"]) if e is not None]
        item["best_edge"] = max(edges) if edges else None
        enriched.append(item)
    enriched.sort(key=lambda x: (x["best_edge"] is None, -(x["best_edge"] or 0)))
    return jsonify({"key": key, "label": cfg["label"], "spot": round(spot, 4) if spot else None,
                    "markets": enriched})


@app.route("/api/simulate/price")
def api_simulate_price():
    """Monte Carlo a coin or commodity forward; return the outcome distribution."""
    import simulate
    kind = request.args.get("kind", "crypto")
    key = request.args.get("key", "BTC")
    try:
        horizon = float(request.args.get("horizon", 60))
        th = request.args.get("threshold")
        threshold = float(th) if th not in (None, "", "null") else None
        direction = request.args.get("direction", "above")
    except ValueError:
        return jsonify({"error": "bad params"}), 400
    try:
        if kind == "commodity":
            import commodities
            spot = commodities.get_spot(key)
            candles = commodities.get_candles(key)  # daily -> horizon in days
        else:
            spot = prices.get_spot(key.upper())
            candles = prices.get_candles(key.upper(), granularity=60)  # horizon in minutes
    except Exception as e:
        return jsonify({"error": f"feed failed: {e}"}), 502
    n = tiers.cap_sims(_tier(), request.args.get("sims", 20000))
    res = simulate.price_sim(spot, candles, horizon, n=n,
                             threshold=threshold, direction=direction)
    res["kind"] = kind
    res["key"] = key
    return jsonify(res)


@app.route("/api/simulate/game")
def api_simulate_game():
    """Simulate a baseball game many times -> win %, totals, blowout/shutout."""
    import simulate, datetime as _dt
    date = request.args.get("date") or _dt.date.today().isoformat()
    try:
        game_pk = int(request.args["game_pk"])
    except (KeyError, ValueError):
        return jsonify({"error": "game_pk required"}), 400
    try:
        games = baseball.analyze_slate(date, date[:4])
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    g = next((x for x in games if x["game_pk"] == game_pk), None)
    if not g:
        return jsonify({"error": "game not found"}), 404
    n = tiers.cap_sims(_tier(), request.args.get("sims", 20000))
    res = simulate.game_sim(g["exp_runs_home"], g["exp_runs_away"], n=n)
    res.update(matchup=g["matchup"], home=g["home_name"], away=g["away_name"])
    return jsonify(res)


@app.route("/api/simulate/weather")
def api_simulate_weather():
    import simulate, weather_markets
    city = request.args.get("city", "nyc")
    want_date = request.args.get("date")
    try:
        data = weather_markets.get_city(city)
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    evs = data.get("events") or []
    ev = next((e for e in evs if e.get("date") == want_date), None) if want_date else None
    if ev is None:
        ev = evs[0] if evs else None
    if not ev or not ev.get("model"):
        return jsonify({"error": "no forecast available"}), 404
    m = ev["model"]
    th = request.args.get("threshold")
    threshold = float(th) if th not in (None, "", "null") else None
    n = tiers.cap_sims(_tier(), request.args.get("sims", 20000))
    res = simulate.temp_sim(m["mean"], m["sigma"], n=n, threshold=threshold,
                            direction=request.args.get("direction", "above"))
    res.update(city=data["city"], date=ev["date"], forecast_high=m["forecast_high"])
    return jsonify(res)


@app.route("/api/simulate/dfs", methods=["POST"])
def api_simulate_dfs():
    """Optimize + simulate a DraftKings DFS lineup from a pasted DKSalaries.csv."""
    import simulate
    locked = _locked("dfs")
    if locked:
        return locked
    d = request.get_json(force=True, silent=True) or {}
    text = d.get("csv", "")
    if not text.strip():
        return jsonify({"error": "paste your DraftKings salaries CSV"}), 400
    try:
        roster = int(d.get("roster", 6))
        cap = int(d.get("cap", 50000))
    except (ValueError, TypeError):
        return jsonify({"error": "bad roster/cap"}), 400
    import datetime as _dt
    n = tiers.cap_sims(_tier(), d.get("sims", 20000))
    return jsonify(simulate.dfs_build(
        text, roster=roster, cap=cap, sport=d.get("sport", "ufc"),
        mode=d.get("mode", "classic"), objective=d.get("objective", "projection"),
        date=d.get("date") or _dt.date.today().isoformat(), sims=n))


@app.route("/api/baseball/today")
def api_baseball_today():
    """Model predictions for a day's MLB slate plus parlay combo suggestions."""
    import datetime as _dt
    date = request.args.get("date") or _dt.date.today().isoformat()
    season = request.args.get("season") or date[:4]
    try:
        games = baseball.analyze_slate(date, season)
    except Exception as e:
        return jsonify({"error": f"baseball data failed: {e}"}), 502
    # Record the model's pre-game picks (only for games not yet final) so we can
    # grade the model's real track record later.
    for g in games:
        if (g.get("live") or {}).get("state") != "Final":
            store.record_mlb_pick(g["game_pk"], date,
                                  "home" if g["pick_is_home"] else "away",
                                  g["pick"], g["pick_prob"], g.get("pick_price_cents"))
            # Track the latest pre-game price of our side for closing-line value.
            store.update_mlb_close(g["game_pk"], g.get("pick_price_cents"))
    combos = baseball.build_combos(games)
    return jsonify({"date": date, "games": games, "combos": combos})


@app.route("/api/backtest")
def api_backtest():
    """Replay history to measure how well the crypto model predicts reality."""
    locked = _locked("backtest")
    if locked:
        return locked
    import backtest
    coin = request.args.get("coin", "BTC").upper()
    try:
        horizon = int(request.args.get("horizon", 15))
    except ValueError:
        horizon = 15
    horizon = max(1, min(120, horizon))
    try:
        result = backtest.run(coin, horizon_min=horizon)
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    return jsonify(result)


@app.route("/api/recorder/status")
def api_recorder_status():
    import recorder
    return jsonify(recorder.status())


@app.route("/api/recorder/backtest")
def api_recorder_backtest():
    """Realized P&L of the edge strategy at real recorded Kalshi prices."""
    import recorder
    coin = request.args.get("coin") or None
    timeframe = request.args.get("timeframe") or None
    try:
        return jsonify(recorder.backtest(coin=coin, timeframe=timeframe))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/bets", methods=["GET"])
def api_list_bets():
    return jsonify(store.list_bets())


@app.route("/api/bets", methods=["POST"])
def api_add_bet():
    d = request.get_json(force=True, silent=True) or {}
    try:
        stake = float(d["stake"])
        price = float(d["price_cents"]) if d.get("price_cents") not in (None, "", "null") else None
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({"error": f"bad params: {e}"}), 400
    kind = d.get("kind", "other")
    bid = store.add_bet(kind, d.get("description", ""), d.get("side"),
                        stake, price, d.get("notes"))
    return jsonify({"id": bid}), 201


@app.route("/api/bets/<int:bet_id>/settle", methods=["POST"])
def api_settle_bet(bet_id):
    d = request.get_json(force=True, silent=True) or {}
    m = store.settle_bet(bet_id, d.get("status", ""))
    if m is None:
        return jsonify({"error": "bad status or bet not found"}), 400
    return jsonify(m)


@app.route("/api/bets/<int:bet_id>", methods=["DELETE"])
def api_delete_bet(bet_id):
    store.delete_bet(bet_id)
    return jsonify({"ok": True})


@app.route("/api/sports/meta")
def api_sports_meta():
    import sports
    return jsonify({k: v["label"] for k, v in sports.SPORTS.items()})


@app.route("/api/sports/<sport_key>")
def api_sports(sport_key):
    import sports
    try:
        events = sports.get_events(sport_key)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"kalshi fetch failed: {e}"}), 502
    grid = None
    # Racing: overlay an independent grid-based win model to surface real edge
    # (Pro+). Falls back silently to the de-vig favorite for free users.
    if sport_key in ("nascar", "f1") and tiers.has_feature(_tier(), "racing_picks"):
        try:
            import racing
            import datetime as _dt
            events, grid = racing.race_board(sport_key, events,
                                             date=_dt.date.today().isoformat())
        except Exception:
            grid = None
    return jsonify({"sport": sport_key, "events": events, "grid": grid,
                    "racing_locked": (sport_key in ("nascar", "f1")
                                      and not tiers.has_feature(_tier(), "racing_picks"))})


@app.route("/api/weather/meta")
def api_weather_meta():
    import weather_markets
    return jsonify({k: v["label"] for k, v in weather_markets.CITIES.items()})


@app.route("/api/weather/<city>")
def api_weather_city(city):
    import weather_markets
    try:
        return jsonify(weather_markets.get_city(city))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"fetch failed: {e}"}), 502


@app.route("/api/combine/meta")
def api_combine_meta():
    import combine
    return jsonify(combine.CATEGORIES)


@app.route("/api/combine")
def api_combine():
    """Cross-category parlay (MLB + daily crypto + UFC/tennis/golf/soccer/WNBA),
    tuned to a target per-leg confidence."""
    import datetime as _dt
    import combine
    date = request.args.get("date") or _dt.date.today().isoformat()
    season = date[:4]
    cats = [c for c in (request.args.get("cats", "") or "").split(",") if c]
    if not cats:
        cats = list(combine.CATEGORIES.keys())
    try:
        legs = tiers.cap_legs(_tier(), request.args.get("legs", 3))
        target = float(request.args.get("target", 65))
        payout = request.args.get("payout")
        payout = float(payout) if payout not in (None, "", "0") else None
    except ValueError:
        return jsonify({"error": "bad legs/target"}), 400
    try:
        return jsonify(combine.build(cats, legs, target, date, season, target_payout=payout,
                                     max_legs=tiers.cap_legs(_tier(), 30)))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/baseball/parlay")
def api_baseball_parlay():
    """Build an N-leg parlay tuned to a target per-leg confidence, picking the
    optimal line (hits 1+/2+, run total, moneyline/spread) for each leg."""
    import datetime as _dt
    date = request.args.get("date") or _dt.date.today().isoformat()
    season = request.args.get("season") or date[:4]
    try:
        legs = tiers.cap_legs(_tier(), request.args.get("legs", 3))
        target = float(request.args.get("target", 65))
        payout = request.args.get("payout")
        payout = float(payout) if payout not in (None, "", "0") else None
    except ValueError:
        return jsonify({"error": "bad legs/target"}), 400
    try:
        games = baseball.analyze_slate(date, season)
    except Exception as e:
        return jsonify({"error": f"baseball data failed: {e}"}), 502
    combo = baseball.build_target_parlay(games, legs, target, target_payout=payout,
                                         max_legs=tiers.cap_legs(_tier(), 30))
    return jsonify({"combo": combo})


@app.route("/api/baseball/sgp")
def api_baseball_sgp():
    """Same-game parlays with correlation-aware (simulated) joint odds. Legs from
    one game are correlated, so these are read off a full game simulation rather
    than multiplying independent marginals."""
    import datetime as _dt
    locked = _locked("same_game_parlay")
    if locked:
        return locked
    date = request.args.get("date") or _dt.date.today().isoformat()
    season = request.args.get("season") or date[:4]
    try:
        legs = tiers.cap_legs(_tier(), request.args.get("legs", 3))
        target = float(request.args.get("target", 55))
        payout = request.args.get("payout")
        payout = float(payout) if payout not in (None, "", "0") else 0
        sims = tiers.cap_sims(_tier(), request.args.get("sims", 5000))
    except ValueError:
        return jsonify({"error": "bad legs/target"}), 400
    try:
        games = baseball.analyze_slate(date, season)
    except Exception as e:
        return jsonify({"error": f"baseball data failed: {e}"}), 502
    res = baseball.build_same_game_parlays(games, n_legs=legs, target_pct=target,
                                           target_payout=payout, n_sims=sims)
    return jsonify(res)


@app.route("/api/baseball/mixed")
def api_baseball_mixed():
    """One parlay across multiple games that may stack correlated legs in a game
    and add single legs from others. Within a game -> simulated joint odds;
    across games -> independent product."""
    import datetime as _dt
    locked = _locked("mixed_parlay")
    if locked:
        return locked
    date = request.args.get("date") or _dt.date.today().isoformat()
    season = request.args.get("season") or date[:4]
    try:
        legs = tiers.cap_legs(_tier(), request.args.get("legs", 4))
        target = float(request.args.get("target", 55))
        payout = request.args.get("payout")
        payout = float(payout) if payout not in (None, "", "0") else 0
        sims = tiers.cap_sims(_tier(), request.args.get("sims", 5000))
        max_total = tiers.cap_legs(_tier(), 12)
    except ValueError:
        return jsonify({"error": "bad legs/payout"}), 400
    try:
        games = baseball.analyze_slate(date, season)
    except Exception as e:
        return jsonify({"error": f"baseball data failed: {e}"}), 502
    item = baseball.build_mixed_parlay(games, n_legs=legs, target_pct=target,
                                       target_payout=payout, n_sims=sims,
                                       max_total_legs=max_total)
    return jsonify({"parlay": item})


@app.route("/api/baseball/record")
def api_baseball_record():
    try:
        baseball.grade_picks()
    except Exception:
        pass
    return jsonify(store.mlb_record())


@app.route("/api/stats")
def api_stats():
    return jsonify(store.stats())


if __name__ == "__main__":
    import os
    import argparse
    # Works the same in any shell:  python app.py --debug --port 8080
    parser = argparse.ArgumentParser(description="Kalshi betting helper server")
    parser.add_argument("--debug", action="store_true",
                        help="auto-reload on file changes (no manual restart needed)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5000)))
    args = parser.parse_args()
    # --debug flag OR DEBUG=1 env var both enable auto-reload.
    debug = args.debug or os.environ.get("DEBUG") == "1"
    # Start the recorder now (skip in the reloader's watcher parent to avoid two).
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        _ensure_recorder()
    app.run(host="0.0.0.0", port=args.port, debug=debug, use_reloader=debug)
