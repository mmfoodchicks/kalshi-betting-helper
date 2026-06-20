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

import time

from flask import Flask, jsonify, request, render_template

import prices
import odds
import store
import kalshi
import baseball

app = Flask(__name__)
store.init_db()


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
    return jsonify({"coin": coin, "timeframe": timeframe, "spot": round(spot, 2),
                    "markets": enriched})


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
    combos = baseball.build_combos(games)
    return jsonify({"date": date, "games": games, "combos": combos})


@app.route("/api/stats")
def api_stats():
    return jsonify(store.stats())


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
