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
import clock
import hmac
import time
import threading

from flask import Flask, jsonify, request, render_template, Response
from werkzeug.middleware.proxy_fix import ProxyFix

import prices
import odds
import store
import kalshi
import baseball
import tiers

app = Flask(__name__)
# Respect the X-Forwarded-* headers from the hosting platform's reverse proxy so
# request.is_secure / scheme are correct (needed for HSTS + secure cookies).
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
# Used by Flask for signing if we add sessions later; pulled from the env in prod.
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)
# Changes every server start so the browser re-fetches CSS/JS after an update.
_ASSET_VERSION = str(int(time.time()))
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _asset_version():
    """Cache-busting token = the newest mtime of the front-end files. Changes the
    moment app.js/style.css change on disk (e.g. after `git pull`), so a plain
    page reload fetches the new UI without needing a server restart."""
    try:
        latest = max(os.path.getmtime(os.path.join(_STATIC_DIR, f))
                     for f in ("app.js", "style.css", "sw.js"))
        return str(int(latest))
    except OSError:
        return _ASSET_VERSION
# Don't sort JSON keys: it's wasted work and crashes on any dict with mixed
# key types (e.g. integer prop lines alongside string keys).
app.json.sort_keys = False
store.init_db()

# Optional: auto-pull the branch + restart when new commits land, so a push goes
# live with no manual step (set VIGIL_SELFUPDATE=1). No-ops on Render / anywhere
# without a git checkout — that path uses the platform's own autoDeploy.
try:
    import selfupdate
    selfupdate.start()
except Exception:
    pass

# Prediction logger: capture each sim's Kalshi predictions and grade them when the
# market settles, so tennis/UFC (and more as they're wired) accrue the graded
# history the calibrator needs. Background, cheap (boards are cached).
try:
    import predlog
    predlog.start()
except Exception:
    pass


@app.after_request
def _security_headers(resp):
    """Baseline hardening headers for a public deployment."""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    # Only assert HSTS once we're actually being served over HTTPS.
    if request.is_secure:
        resp.headers.setdefault("Strict-Transport-Security",
                                "max-age=31536000; includeSubDomains")
    return resp


@app.route("/healthz")
def _healthz():
    return Response("ok", mimetype="text/plain")


@app.route("/api/version")
def _api_version():
    """Current build token so an open page can detect a deploy and offer a
    one-tap refresh. Changes whenever the front-end files change on disk (after a
    pull/redeploy)."""
    return jsonify({"v": _asset_version()})


@app.route("/robots.txt")
def _robots():
    # Keep the app out of search indexes while it's pre-launch / private.
    return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")

# Optional password protection (recommended when exposing it over a tunnel).
# Set APP_PASSWORD (and optionally APP_USER) in the environment to turn it on.
APP_USER = os.environ.get("APP_USER", "kalshi")
APP_PASSWORD = os.environ.get("APP_PASSWORD")


@app.before_request
def _auth():
    if not APP_PASSWORD:
        return
    if request.path in ("/healthz", "/robots.txt"):   # platform probes, no creds
        return
    a = request.authorization
    # Constant-time compare so a wrong password can't be teased out by timing.
    ok = a and hmac.compare_digest(str(a.username or ""), APP_USER) \
           and hmac.compare_digest(str(a.password or ""), APP_PASSWORD)
    if not ok:
        return Response("Login required", 401,
                        {"WWW-Authenticate": 'Basic realm="Vigil"'})


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
        try:
            import mlb_recorder
            mlb_recorder.start_background()
        except Exception:
            pass
        try:
            _init_deep_sims()
        except Exception:
            pass


def _init_deep_sims():
    """Register the heavy season sims with the nightly cache/scheduler, reload any
    persisted result from disk, and start the background refresh (reruns each job
    once per day just after local midnight)."""
    import deep_cache
    import datetime as _dt

    def run_mlb():
        import deep_season
        season = str(clock.today_et().year)
        # Capture the profiles this run actually used, so the day's history is
        # diffed against the same rosters the numbers came from rather than a
        # second fetch that may have moved underneath us.
        profs = {}
        agg = deep_season.run_deep(season, n_seasons=4000, ret_profiles=profs)
        _deep["agg"] = agg
        _deep["season"] = season
        # Snapshot + attribution. Best-effort by design: the deep run is the
        # product, and a history failure must never cost the night's numbers.
        try:
            import deep_history
            deep_history.build_day(agg, season, profs)
        except Exception:
            pass
        return {"agg": agg, "season": season}

    def run_f1():
        import racing_sim
        return racing_sim.sim_f1(3000)

    def run_nascar():
        import racing_sim
        return racing_sim.sim_nascar(3000)

    def run_pro(league):
        def run():
            import pro_sim
            return pro_sim.project(league, 4000)
        return run

    def run_nfl_season():
        import nfl_season
        return nfl_season.run_season(n=4000)

    def run_cfb():
        import cfb
        return cfb.run_season(n=4000)

    def run_model_trust():
        """Re-measure how far each sport's model may depart from the market, by
        replaying point-in-time history. Stores the fitted blend weights."""
        import model_trust
        model_trust.refresh()
        return model_trust.load()

    deep_cache.register("mlb_deep", run_mlb)
    deep_cache.register("f1", run_f1)
    deep_cache.register("nascar", run_nascar)
    deep_cache.register("nfl_season", run_nfl_season)
    deep_cache.register("cfb", run_cfb)
    deep_cache.register("model_trust", run_model_trust)
    # Every pro-league season is now played out game by game with its own engine
    # (drive-level football, possession basketball, shot-event hockey). WNBA runs
    # in-season; NBA/NHL light up once their upcoming-season schedules publish
    # (their run returns None until then).
    for _lg in ("nfl", "nba", "nhl", "wnba"):
        deep_cache.register(_lg, run_pro(_lg))
    # Restore the MLB deep run from disk so a restart doesn't lose it.
    payload, _ts = deep_cache.load("mlb_deep")
    if payload:
        _deep["agg"] = payload.get("agg")
        _deep["season"] = payload.get("season")
    # This host has no persistent disk, so the deep cache starts empty after every
    # restart and redeploy. Pull the repo's copy of the run history back before
    # the scheduler starts, so a restart doesn't silently empty the calendar.
    # Best-effort: a failure here leaves the app exactly as it was.
    def _restore_history():
        try:
            import deep_history
            deep_history.restore_from_github()
        except Exception:
            pass
    threading.Thread(target=_restore_history, daemon=True).start()
    deep_cache.start_scheduler()
    # Warm the in-season / preseason deep runs in the background if not cached.
    for _k in ("nfl", "nfl_season", "wnba", "cfb"):
        try:
            if deep_cache.load(_k)[0] is None:
                deep_cache.run_job(_k)
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
    # Cache-bust static assets by their on-disk mtime, so a `git pull` alone
    # busts the cache (no server restart needed), and tell the browser not to
    # cache the page itself, so a reload always pulls the latest UI.
    resp = Response(render_template("index.html", v=_asset_version()))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/sw.js")
def service_worker():
    # Served from root so the service worker's scope covers the whole app. Never
    # cache the SW script itself, so a new version is picked up on next load.
    resp = app.send_static_file("sw.js")
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


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
        sig = odds.kalshi_signal(spot, candles, m, mins, calibrated=True)
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
        sig = odds.kalshi_signal(spot, candles, m, days, calibrated=True)  # day-based units
        item = dict(m)
        item["days_to_close"] = round(days, 1)
        item["signal"] = sig
        # Liquidity: an "edge" off a thin/untraded contract is a mirage.
        spread = (round(m["yes_ask"] - m["yes_bid"], 1)
                  if (m.get("yes_ask") is not None and m.get("yes_bid") is not None) else None)
        item["spread"] = spread
        vol = m.get("volume") or 0
        item["thin"] = (spread is None or spread >= 10 or vol < 20)
        edges = [e for e in (sig["edge_yes_cents"], sig["edge_no_cents"]) if e is not None]
        item["best_edge"] = max(edges) if edges else None
        enriched.append(item)
    enriched.sort(key=lambda x: (x["best_edge"] is None, -(x["best_edge"] or 0)))
    basis = commodities.basis_check(spot, markets)
    return jsonify({"key": key, "label": cfg["label"], "spot": round(spot, 4) if spot else None,
                    "basis": basis, "markets": enriched})


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
    date = request.args.get("date") or clock.today_et().isoformat()
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
    # When lineups are posted, also run the full player-level base-running sim
    # (per-batter hits/HR/SB/DK points, speed + steals) and attach it.
    if (g.get("props") or {}).get("batters_home") or (g.get("props") or {}).get("batters_away"):
        try:
            import mlb_sim
            res["player_sim"] = mlb_sim.summary(mlb_sim.simulate(g, min(8000, n)), g=g)
        except Exception:
            pass
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


@app.route("/api/dfs/slate")
def api_dfs_slate():
    """Tonight's DraftKings slate for a sport, straight from DK's public lobby —
    salaries, roster slots and the game/bout key, plus the posted contests (entry
    fee, field size, prize pool) so the contest sim's parameters match the contest
    you're entering. Removes the hand-pasted CSV step entirely.

    ?sport=ufc|mlb|nfl|nba|nhl|golf|nascar|f1|soccer|lol
    ?dg=<draft_group_id>   pick a specific slate (default: the soonest, biggest)
    ?exclude=Dulatov vs Turman,...  drop a postponed game/bout DK hasn't pulled"""
    import dk
    sport = (request.args.get("sport") or "ufc").lower()
    if sport not in dk.SPORTS:
        return jsonify({"error": f"unknown sport (have: {', '.join(sorted(dk.SPORTS))})"}), 400
    dg = request.args.get("dg")
    excl = [s.strip() for s in (request.args.get("exclude") or "").split(",") if s.strip()]
    try:
        s = dk.slate_for(sport, draft_group_id=(int(dg) if dg else None), exclude_games=excl)
    except Exception as e:
        return jsonify({"error": f"dk slate failed: {e}"}), 502
    if not s:
        return jsonify({"error": f"no DraftKings slate posted for {sport} right now"}), 404
    return jsonify(s)


@app.route("/api/simulate/dfs", methods=["POST"])
def api_simulate_dfs():
    """Optimize + simulate a DraftKings DFS lineup from a pasted DKSalaries.csv."""
    import simulate
    locked = _locked("dfs")
    if locked:
        return locked
    d = request.get_json(force=True, silent=True) or {}
    text = d.get("csv", "")
    # No CSV pasted? Pull the live slate from DK's public lobby instead, so the
    # builder works straight from the sport (and auto-drops anyone DK has marked
    # unavailable — a scratched player or a postponed fight).
    auto_slate = None
    if not text.strip():
        try:
            import dk
            sp = (d.get("sport") or "ufc").lower()
            if sp in dk.SPORTS:
                excl = d.get("exclude") or []
                if isinstance(excl, str):
                    excl = [x.strip() for x in excl.split(",") if x.strip()]
                got = dk.slate_for(sp, draft_group_id=d.get("draft_group_id"),
                                   exclude_games=excl)
                if got:
                    text = got["csv"]
                    auto_slate = {k: got[k] for k in
                                  ("draft_group_id", "n_players", "n_dropped", "dropped")}
        except Exception:
            auto_slate = None
    if not text.strip():
        return jsonify({"error": "paste your DraftKings salaries CSV "
                                 "(or pass a sport to auto-load tonight's slate)"}), 400
    try:
        roster = int(d.get("roster", 6))
        cap = int(d.get("cap", 50000))
    except (ValueError, TypeError):
        return jsonify({"error": "bad roster/cap"}), 400
    import datetime as _dt
    n = tiers.cap_sims(_tier(), d.get("sims", 20000))
    if d.get("sport") == "lol":
        # LoL Classic DFS: captain + role optimizer with team-correlated ceiling.
        import lol_dfs

        def _li(key, default, cast=float):
            try:
                return cast(d.get(key, default))
            except (ValueError, TypeError):
                return default
        obj = d.get("objective")
        obj = obj if obj in ("projection", "ceiling", "leverage") else "projection"
        contest = d.get("contest") if d.get("contest") in ("gpp", "double_up") else None
        try:
            return jsonify(lol_dfs.build(
                text, objective=obj, contest=contest,
                contest_size=(int(_li("contest_size", 0, int)) or None),
                entry_fee=max(0.01, _li("entry_fee", 1.0)),
                prize_pool=(_li("prize_pool", 0.0) or None),
                first_prize=(_li("first_prize", 0.0) or None)))
        except Exception as e:
            return jsonify({"error": f"lol dfs failed: {e}"}), 502
    if d.get("sport") == "nfl":
        # NFL Classic DFS: positional optimizer + Sleeper-seeded correlated contest sim.
        import nfl_dfs

        def _ni(key, default, cast=float):
            try:
                return cast(d.get(key, default))
            except (ValueError, TypeError):
                return default
        obj = d.get("objective")
        obj = obj if obj in ("projection", "ceiling", "leverage") else "projection"
        contest = d.get("contest") if d.get("contest") in ("gpp", "double_up") else None
        try:
            return jsonify(nfl_dfs.build(
                text, week=max(1, min(18, int(_ni("week", 1, int)))), objective=obj,
                stack=d.get("stack", True) is not False, contest=contest,
                contest_size=(int(_ni("contest_size", 0, int)) or None),
                entry_fee=max(0.01, _ni("entry_fee", 1.0)),
                prize_pool=(_ni("prize_pool", 0.0) or None),
                first_prize=(_ni("first_prize", 0.0) or None)))
        except Exception as e:
            return jsonify({"error": f"nfl dfs failed: {e}"}), 502
    if d.get("sport") == "mlb":
        # Baseball DFS is driven by the game simulator (correlated hitter ceilings
        # for stacking) -- a different path from the projection-based racing/UFC.
        import mlb_dfs
        date = d.get("date") or clock.today_et().isoformat()
        objective = "ceiling" if d.get("objective") == "ceiling" else "median"

        def _i(key, default, lo, hi):
            try:
                return max(lo, min(hi, int(d.get(key, default))))
            except (ValueError, TypeError):
                return default

        def _f(key, default, lo, hi):
            try:
                return max(lo, min(hi, float(d.get(key, default))))
            except (ValueError, TypeError):
                return default

        n_lineups = _i("lineups", 1, 1, 150)
        contest = d.get("contest") if d.get("contest") in ("gpp", "double_up") else None
        return jsonify(mlb_dfs.build(
            date, text, cap=cap, objective=objective, n_sims=min(6000, n),
            n_lineups=n_lineups, max_exposure=_f("max_exposure", 60, 5, 100),
            min_uniq=_i("min_uniq", 2, 1, 6), stack_min=_i("stack_min", 4, 0, 5),
            contest=contest, field_size=_i("field_size", 600, 100, 1200),
            contest_iters=_i("contest_iters", 500, 50, 800),
            entry_fee=_f("entry_fee", 1.0, 0.01, 100000),
            contest_size=_i("contest_size", 0, 0, 5000000) or None,
            prize_pool=(_f("prize_pool", 0, 0, 1e9) or None),
            first_prize=(_f("first_prize", 0, 0, 1e9) or None),
            include_unconfirmed=bool(d.get("include_unconfirmed"))))
    # UFC / F1 / NASCAR: single or multi-lineup portfolio + large-field contest sim.
    def _num(key, default, cast=float):
        try:
            return cast(d.get(key, default))
        except (ValueError, TypeError):
            return default
    contest = d.get("contest") if d.get("contest") in ("gpp", "double_up") else None
    built = simulate.dfs_build(
        text, roster=roster, cap=cap, sport=d.get("sport", "ufc"),
        mode=d.get("mode", "classic"), objective=d.get("objective", "projection"),
        date=d.get("date") or clock.today_et().isoformat(), sims=n,
        n_lineups=max(1, min(150, _num("lineups", 1, int))),
        max_exposure=max(5.0, min(100.0, _num("max_exposure", 60.0))),
        min_uniq=max(1, min(6, _num("min_uniq", 1, int))),
        contest=contest,
        contest_size=(int(_num("contest_size", 0, int)) or None),
        entry_fee=max(0.01, _num("entry_fee", 1.0)),
        prize_pool=(_num("prize_pool", 0.0) or None),
        first_prize=(_num("first_prize", 0.0) or None),
        grid_text=(d.get("grid") or None))
    if auto_slate and isinstance(built, dict):
        built["dk_slate"] = auto_slate          # what was auto-loaded, and who DK dropped
    return jsonify(built)


def _prop_types():
    """Optional prop-type filter from ?types=ML,Total,Hit,... (None = all types)."""
    raw = request.args.get("types")
    if not raw:
        return None
    got = {t.strip() for t in raw.split(",") if t.strip()}
    return got or None


@app.route("/api/calibration")
def api_calibration():
    """Site-wide calibration audit: per-model temperature, sample size, and
    direction (reined in / sharpened / well-calibrated / accruing / no data)."""
    import calibrate
    try:
        return jsonify(calibrate.report())
    except Exception as e:
        return jsonify({"error": f"calibration report failed: {e}"}), 502


@app.route("/api/baseball/today")
def api_baseball_today():
    """Model predictions for a day's MLB slate plus parlay combo suggestions."""
    import datetime as _dt
    date = request.args.get("date") or clock.today_et().isoformat()
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
                                  g["pick"], g["pick_prob"], g.get("pick_price_cents"),
                                  pred_total=g.get("exp_total"),
                                  p_home_model=g.get("p_home_model"),
                                  p_home_deep=g.get("p_home_deep"),
                                  prob_raw=g.get("pick_prob_raw"))
            # Track the latest pre-game price of our side for closing-line value.
            store.update_mlb_close(g["game_pk"], g.get("pick_price_cents"))
    combos = baseball.build_combos(games, types=_prop_types(), allow_live=_allow_live())
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
                                             date=clock.today_et().isoformat())
        except Exception:
            grid = None
    return jsonify({"sport": sport_key, "events": events, "grid": grid,
                    "racing_locked": (sport_key in ("nascar", "f1")
                                      and not tiers.has_feature(_tier(), "racing_picks"))})


_live_cache = {"ts": 0.0, "data": None}


@app.route("/api/sports/live")
def api_sports_live():
    """Every game we track that's being played RIGHT NOW, confirmed from each
    league's live scoreboard (not market-timing guesses). MLB carries its richer
    inning/base-out feed; the other tracked leagues come from ESPN scoreboards and
    surface the moment a game flips to in-progress."""
    import time as _t
    import datetime as _dt
    now = _t.time()
    if _live_cache["data"] and now - _live_cache["ts"] < 30:
        return jsonify(_live_cache["data"])
    out = []
    # MLB: richer dedicated feed (inning + score).
    try:
        today = clock.today_et().isoformat()
        for g in baseball._schedule(today, today[:4]):
            lv = g.get("live") or {}
            if lv.get("state") == "Live":
                out.append({
                    "sport": "⚾ MLB", "confirmed": True,
                    "title": f"{g['away_name']} @ {g['home_name']}",
                    "detail": f"{lv.get('inning_state', '')} {lv.get('inning', '')}".strip(),
                    "score": f"{lv.get('away_runs', 0)}–{lv.get('home_runs', 0)}",
                    "nav": {"tab": "baseball", "pk": g["game_pk"]},
                })
    except Exception:
        pass
    # Every other tracked team sport, confirmed live from its ESPN scoreboard.
    try:
        import live as _live
        out.extend(_live.confirmed_live())
    except Exception:
        pass
    data = {"games": out, "confirmed_count": sum(1 for g in out if g["confirmed"])}
    _live_cache.update(ts=now, data=data)
    return jsonify(data)


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


def _allow_live():
    """Whether the caller opted in to betting games already under way. Off unless
    explicitly ticked -- a live board is priced from a snapshot that is seconds
    old, so it must never be the silent default."""
    v = (request.args.get("live") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


@app.route("/api/futures/modeled")
def api_futures_modeled():
    """Long-dated markets our season simulators actually model, ranked by
    expected return to settlement.

    Unlike /api/futures (market-only, no edge claim), these rows carry a model
    number, so expected return can be genuinely positive. It is blended with the
    market at the weight that sport's model has EARNED on graded results."""
    import mfutures as _mf
    def num(name, default=None, lo=None, hi=None):
        v = request.args.get(name)
        if v in (None, ""):
            return default
        try:
            x = float(v)
        except ValueError:
            return default
        if lo is not None:
            x = max(lo, x)
        if hi is not None:
            x = min(hi, x)
        return x
    def flag(name, default=False):
        v = request.args.get(name)
        if v in (None, ""):
            return default
        return v.strip().lower() in ("1", "true", "yes", "on")
    try:
        data = _mf.board(
            q=request.args.get("q", ""),
            sort=request.args.get("sort", "best"),
            sports=[x for x in (request.args.get("sports", "") or "").split(",") if x],
            markets=[x for x in (request.args.get("markets", "") or "").split(",") if x],
            min_prob=num("min_prob", 0, 0, 100),
            max_days=num("max_days", None, 1, 40000),
            limit=int(num("limit", 60, 1, 400)),
            include_suspect=flag("include_suspect", False),
            positive_only=flag("positive_only", True),
            in_season_only=flag("in_season", False),
        )
        if data.get("building"):
            return jsonify({"building": True,
                            "error": "running the season simulations — retry shortly"}), 202
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": f"modeled futures failed: {e}"}), 502


@app.route("/api/futures")
def api_futures():
    """Long-dated Kalshi contracts, ranked by what they pay per year.

    Deliberately market-only: no model, no edge claim. The board answers "given
    how likely this is and how long my money is tied up, what does it pay?" and
    shows the loss chance next to every yield."""
    import futures as _fut
    q = request.args.get("q", "")
    sort = request.args.get("sort", "best")

    def num(name, default=None, lo=None, hi=None):
        v = request.args.get(name)
        if v in (None, ""):
            return default
        try:
            x = float(v)
        except ValueError:
            return default
        if lo is not None:
            x = max(lo, x)
        if hi is not None:
            x = min(hi, x)
        return x
    try:
        data = _fut.board(
            q=q, sort=sort,
            min_prob=num("min_prob", _fut.DEFAULT_MIN_PROB, 1, 100),
            max_days=num("max_days", None, 1, 40000),
            min_days=num("min_days", _fut.MIN_DAYS, 1, 40000),
            min_volume=num("min_volume", None, 0, 1e9),
            limit=int(num("limit", 60, 1, 500)),
        )
        if data.get("building"):
            # First hit after a cold start: the sweep is ~40 pages and runs on a
            # background thread rather than holding the request open.
            return jsonify({"building": True,
                            "error": "scanning Kalshi's long-dated markets — retry shortly"}), 202
        data["summary"] = _fut.summary()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": f"futures load failed: {e}"}), 502


@app.route("/api/combine/meta")
def api_combine_meta():
    import combine
    return jsonify({"categories": combine.CATEGORIES, "types": combine.CATEGORY_TYPES})


@app.route("/api/combine")
def api_combine():
    """Cross-category parlay (MLB + daily crypto + UFC/tennis/golf/soccer/WNBA),
    tuned to a target per-leg confidence."""
    import datetime as _dt
    import combine
    date = request.args.get("date") or clock.today_et().isoformat()
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
    # Each target (legs / payout) is "require" (hard) / "prefer" (recommend) /
    # "off", combined by conn ('and'/'or') -- same controls as the baseball maker.
    modes = ("require", "prefer", "off")
    legs_mode = request.args.get("legs_mode", "prefer")
    payout_mode = request.args.get("payout_mode")
    conn = "and" if request.args.get("conn") == "and" else "or"
    if legs_mode not in modes:
        legs_mode = "prefer"
    if payout_mode is not None and payout_mode not in modes:
        payout_mode = None
    # Per-sport leg-type filter (the maker's type chips). Present-but-empty means
    # every catalogued type is turned off; absent means no filtering.
    types = ([t for t in request.args.get("types", "").split(",") if t]
             if "types" in request.args else None)
    # Per-sport leg budget: "mlb:0,tennis:2" -> {mlb: 0 (all), tennis: 2}. When
    # present, the combo is built from these counts per sport instead of one floor.
    per_cat = {}
    for pair in (request.args.get("per_cat", "") or "").split(","):
        if ":" in pair:
            k, _, v = pair.partition(":")
            try:
                if k.strip():
                    per_cat[k.strip()] = max(0, min(40, int(v)))
            except ValueError:
                continue
    try:
        return jsonify(combine.build(cats, legs, target, date, season, target_payout=payout,
                                     max_legs=tiers.cap_legs(_tier(), 30),
                                     legs_mode=legs_mode, payout_mode=payout_mode, conn=conn,
                                     types=types, per_cat=(per_cat or None),
                                     allow_live=_allow_live()))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/combine/recommended")
def api_combine_recommended():
    """Auto-built safest / best-value / best parlays from the checked sports."""
    import datetime as _dt
    import combine
    date = request.args.get("date") or clock.today_et().isoformat()
    season = date[:4]
    cats = [c for c in (request.args.get("cats", "") or "").split(",") if c]
    if not cats:
        return jsonify({"error": "no_cats", "counts": {}})
    types = ([t for t in request.args.get("types", "").split(",") if t]
             if "types" in request.args else None)
    try:
        return jsonify(combine.recommended(cats, date, season,
                                           max_legs=tiers.cap_legs(_tier(), 30),
                                           types=types, allow_live=_allow_live()))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/baseball/parlay")
def api_baseball_parlay():
    """Build an N-leg parlay tuned to a target per-leg confidence, picking the
    optimal line (hits 1+/2+, run total, moneyline/spread) for each leg."""
    import datetime as _dt
    date = request.args.get("date") or clock.today_et().isoformat()
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
                                         max_legs=tiers.cap_legs(_tier(), 30), types=_prop_types(),
                                         allow_live=_allow_live())
    return jsonify({"combo": combo})


@app.route("/api/baseball/season")
def api_baseball_season():
    """Our season Monte Carlo (division/playoff/pennant/WS odds + win totals) and
    the resulting edges vs Kalshi futures markets."""
    import datetime as _dt
    import season_sim
    season = request.args.get("season") or str(clock.today_et().year)
    try:
        sims = tiers.cap_sims(_tier(), request.args.get("sims", 4000))
    except ValueError:
        return jsonify({"error": "bad params"}), 400
    try:
        sim = season_sim.cached(season, n=sims)
        fut = season_sim.futures_edges(season, sim=sim)
    except Exception as e:
        return jsonify({"error": f"season sim failed: {e}"}), 502
    teams = [{k: v for k, v in t.items() if k != "_wins_sample"} for t in sim["teams"]]
    return jsonify({"season": season, "n_sims": sim["n_sims"],
                    "n_games_left": sim["n_games_left"], "teams": teams,
                    "futures": fut["edges"], "futures_summary": fut["summary"],
                    "n_liquid": fut.get("n_liquid")})


@app.route("/api/baseball/futures")
def api_baseball_futures():
    """Futures board: pick a market (World Series / pennant / division / playoffs /
    win-total line) and get the full ranked team list — how many of the N sims
    each team won it, our model %, Kalshi and Polymarket. Built for the dropdown +
    search UI."""
    import datetime as _dt
    import season_sim
    season = request.args.get("season") or str(clock.today_et().year)
    try:
        sims = tiers.cap_sims(_tier(), request.args.get("sims", 4000))
    except ValueError:
        return jsonify({"error": "bad params"}), 400
    # The deep 4,000-season pitch-by-pitch run is the headline: serve it by
    # default whenever a completed run is cached. Only ?engine=fast forces the
    # instant Pythagorean board (the cold-start fallback before the first deep run).
    if request.args.get("engine") != "fast":
        agg = _deep.get("agg")
        if agg and _deep.get("season") == season:
            try:
                import deep_cache
                board = season_sim.deep_board(agg, season)
                board["age_sec"] = deep_cache.age("mlb_deep")
                return jsonify(board)
            except Exception as e:
                return jsonify({"error": f"deep board failed: {e}"}), 502
    try:
        board = season_sim.board_cached(season, n=sims)
    except Exception as e:
        return jsonify({"error": f"season sim failed: {e}"}), 502
    return jsonify(board)


@app.route("/api/racing/<sport>")
def api_racing_season(sport):
    """Deep full-season motorsport sim -> championship odds. F1 simulates
    qualifying + race (+ sprints) over the remaining calendar; NASCAR runs the
    playoff bracket. Best-effort Polymarket champion prices attached."""
    import deep_cache
    import time as _t
    sport = (sport or "").lower()
    if sport not in ("f1", "nascar"):
        return jsonify({"error": "unknown sport"}), 404
    # Served from the weekly disk cache (shared by everyone); computed inline on a
    # cold miss since the racing sims are cheap. The scheduler refreshes weekly.
    try:
        data, ts = deep_cache.load(sport)
        if data is None:
            data, ts = deep_cache.run_sync(sport)
    except Exception as e:
        return jsonify({"error": f"sim failed: {e}"}), 502
    if not data:
        return jsonify({"error": "no season data available"}), 502
    data = dict(data)
    data["generated_at"] = ts
    data["age_sec"] = (_t.time() - ts) if ts else None
    # Live Kalshi prices on the championship + upcoming-race markets (model stays
    # weekly-cached; prices fetched fresh per request).
    try:
        import racing_prices
        racing_prices.attach(data)
    except Exception:
        pass
    return jsonify(data)


@app.route("/api/pro/<league>")
def api_pro_league(league):
    """Roster-aware preseason futures projection (NFL now; NBA/NHL when their
    schedules publish). Served from the weekly cache; live Kalshi prices + edges
    attached per request. Returns a 'computing' status while a cold run warms up."""
    import deep_cache
    import time as _t
    league = (league or "").lower()
    if league not in ("nfl", "nba", "nhl", "wnba"):
        return jsonify({"error": "unknown league"}), 404
    data, ts = deep_cache.load(league)
    if data is None:
        started = deep_cache.run_job(league, force=True)
        st = deep_cache.status(league)
        return jsonify({"status": "computing" if (started or st["running"]) else "unavailable",
                        "league": league, "running": st["running"]}), 202
    data = dict(data)
    data["generated_at"] = ts
    data["age_sec"] = (_t.time() - ts) if ts else None
    try:
        import pro_prices
        pro_prices.attach(league, data)
    except Exception:
        pass
    return jsonify(data)


@app.route("/api/nfl/futures")
def api_nfl_futures():
    """NFL futures board (Featured tab): Super Bowl / conference / division /
    playoffs / win-total lines from a 4,000-season Monte Carlo, priced vs the
    live Kalshi futures series. Mirrors /api/baseball/futures exactly."""
    try:
        import nfl_season
        b = nfl_season.futures_board(n=tiers.cap_sims(_tier(), request.args.get("sims", 4000)))
    except Exception as e:
        return jsonify({"error": f"nfl season sim failed: {e}"}), 502
    if not b:
        return jsonify({"error": "NFL schedule not available yet"}), 502
    return jsonify(b)


@app.route("/api/cfb/futures")
def api_cfb_futures():
    """College Football Playoff board: national-championship + make-the-Playoff
    odds from a 4,000-season drive-engine Monte Carlo with the 12-team CFP,
    priced vs Kalshi's college futures. Served from the nightly deep-season
    cache; a cold hit computes a smaller run in-line."""
    try:
        import cfb
        b = cfb.futures_board()
    except Exception as e:
        return jsonify({"error": f"cfb season sim failed: {e}"}), 502
    if not b:
        return jsonify({"error": "CFB schedule not available yet"}), 502
    return jsonify(b)


@app.route("/api/nfl/team")
def api_nfl_team():
    """Per-player projected season stat lines for one NFL team — Sleeper's
    projections preseason, blending in real weekly stats once games are played.
    Needs ?abbr=KC."""
    abbr = (request.args.get("abbr") or "").upper()
    if not abbr:
        return jsonify({"error": "abbr required"}), 400
    try:
        import nfl_season
        return jsonify(nfl_season.team_detail(abbr))
    except Exception as e:
        return jsonify({"error": f"team detail failed: {e}"}), 502


@app.route("/api/nfl/fantasy")
def api_nfl_fantasy():
    """NFL fantasy / best-ball projection pool (the draft-room engine): per-player
    fppg / floor / ceiling / boom / season + draft value (VOR), vets and rookies.
    Cold start computes in the background (202 until ready)."""
    try:
        import nfl_sim
        b = nfl_sim.board()
    except Exception as e:
        return jsonify({"error": f"nfl sim failed: {e}"}), 502
    if not b:
        return jsonify({"status": "computing",
                        "message": "simulating the season for every player…"}), 202
    return jsonify(b)


@app.route("/api/nfl/grade", methods=["POST"])
def api_nfl_grade():
    """Grade one or many best-ball rosters. Body: {names:[...]} for a single team, or
    {teams:[{label, names}]} to grade + rank several (duplicate players allowed
    across teams). `use_llm: true` adds a small-LLM written narrative when an
    ANTHROPIC_API_KEY is configured."""
    try:
        import nfl_sim
        d = request.get_json(force=True) or {}
        use_llm = bool(d.get("use_llm"))
        teams = d.get("teams")
        if isinstance(teams, list) and teams:
            clean = [{"label": str(t.get("label") or "")[:40],
                      "names": [str(n) for n in (t.get("names") or [])][:40],
                      "pitch": str(t.get("pitch") or "")[:2000]}
                     for t in teams[:16]]
            res = nfl_sim.grade_multi(clean, use_llm=use_llm)
            if res is None:
                return jsonify({"status": "computing", "message": "warming the projection pool…"}), 202
            return jsonify(res)
        names = d.get("names") or []
        if not isinstance(names, list) or not names:
            return jsonify({"error": "send a 'names' list or 'teams'"}), 400
        g, _ = nfl_sim.grade_names([str(n) for n in names][:40], use_llm=use_llm,
                                   pitch=str(d.get("pitch") or "")[:2000])
        if g is None:
            return jsonify({"status": "computing", "message": "warming the projection pool…"}), 202
        return jsonify(g)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


_TEAMS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_teams.json")


@app.route("/api/nfl/teams", methods=["GET", "POST"])
def api_nfl_teams():
    """Persist the drafter's best-ball teams so they survive reloads and don't have
    to be retyped. GET returns saved teams; POST {teams:[{label,names,pitch}]}
    overwrites them. Stored in a gitignored local file."""
    import json
    if request.method == "POST":
        d = request.get_json(force=True, silent=True) or {}
        teams = d.get("teams")
        if not isinstance(teams, list):
            return jsonify({"error": "send a 'teams' list"}), 400
        clean = [{"label": str(t.get("label") or "")[:40],
                  "names": [str(n) for n in (t.get("names") or [])][:40],
                  "pitch": str(t.get("pitch") or "")[:2000]}
                 for t in teams[:16] if (t.get("names") or t.get("label"))]
        try:
            os.makedirs(os.path.dirname(_TEAMS_PATH), exist_ok=True)
            with open(_TEAMS_PATH, "w") as f:
                json.dump({"teams": clean}, f)
        except Exception as e:
            return jsonify({"error": f"save failed: {e}"}), 500
        return jsonify({"saved": len(clean)})
    try:
        with open(_TEAMS_PATH) as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({"teams": []})


@app.route("/api/settings/ai_key", methods=["GET", "POST"])
def api_ai_key():
    """Set up the optional Anthropic key for AI explanations. GET reports whether a
    key is configured (never returns the key). POST saves a pasted key to a local,
    gitignored file and verifies it with a tiny test call."""
    import os
    import nfl_sim
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "anthropic_key.txt")
    if request.method == "GET":
        return jsonify({"configured": nfl_sim.ai_available()})
    d = request.get_json(force=True) or {}
    key = str(d.get("key") or "").strip()
    if not key:                                       # empty -> clear it
        try:
            os.remove(path)
        except OSError:
            pass
        return jsonify({"configured": False, "cleared": True})
    if not key.startswith("sk-ant-"):
        return jsonify({"error": "That doesn't look like an Anthropic key (they start with 'sk-ant-')."}), 400
    # verify with a 1-token call before saving
    try:
        import anthropic
        anthropic.Anthropic(api_key=key).messages.create(
            model="claude-haiku-4-5", max_tokens=1,
            messages=[{"role": "user", "content": "hi"}])
    except Exception as e:
        return jsonify({"error": f"Key didn't work: {str(e)[:160]}"}), 400
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(key)
        os.chmod(path, 0o600)
    except Exception as e:
        return jsonify({"error": f"Couldn't save: {e}"}), 500
    return jsonify({"configured": True, "verified": True})


@app.route("/api/ufc")
def api_ufc():
    """UFC card simulator: per-bout win odds, method/round and each fighter's DK
    projection, with live Kalshi prices + edges. The board (model) is cached; a
    cold start computes in the background and returns 202 until ready."""
    try:
        import ufc_sim
        board = ufc_sim.board()
    except Exception as e:
        return jsonify({"error": f"ufc sim failed: {e}"}), 502
    if not board:
        return jsonify({"status": "computing",
                        "message": "rating every fighter from their fight history…"}), 202
    try:
        import ufc_prices
        ufc_prices.attach(board)
    except Exception:
        pass
    return jsonify(board)


@app.route("/api/lol")
def api_lol():
    """League of Legends esports board: the pro slate with each team's roster,
    per-player projections (from Leaguepedia per-map history), and a DK Pick 6
    board of More/Less kill/assist/CS picks. Cold build is slow (paced, rate-
    limited wiki API); cached 15 min."""
    try:
        import lol
        data = lol.board(max_matches=6)
    except Exception as e:
        return jsonify({"error": f"lol data failed: {e}"}), 502
    if not data:
        return jsonify({"error": "no LoL data available yet (loading / rate-limited — retry shortly)"}), 502
    return jsonify(data)


@app.route("/api/lol/futures")
def api_lol_futures():
    """Championship odds (who wins the split / MSI) for one tournament, from an
    Elo + bracket Monte Carlo. ?page=<OverviewPage>."""
    page = request.args.get("page")
    if not page:
        return jsonify({"error": "missing tournament page"}), 400
    try:
        import lol
        data = lol.sim_tournament(page)
    except Exception as e:
        return jsonify({"error": f"lol futures failed: {e}"}), 502
    if not data:
        return jsonify({"error": "no data for that tournament yet (loading / rate-limited)"}), 502
    return jsonify(data)


@app.route("/api/nfl/week")
def api_nfl_week():
    """NFL week board: modeled score, win%, yards/TDs + key players per game.
    Non-blocking (heavy ESPN fan-out) -- returns 502 while the board builds; the
    frontend polls. ?week=1 (regular season; preseason is skipped)."""
    try:
        week = int(request.args.get("week", 1))
    except ValueError:
        week = 1
    week = max(1, min(18, week))
    try:
        import nfl_live
        data = nfl_live.board(week=week)
    except Exception as e:
        return jsonify({"error": f"nfl data failed: {e}"}), 502
    if not data:
        return jsonify({"error": "no NFL data yet (building in the background — retry shortly)"}), 502
    return jsonify(data)


@app.route("/api/wnba/slate")
def api_wnba_slate():
    """WNBA daily slate: possession-engine win probs vs Kalshi ML/spread/total
    at the exact listed lines, correlated player props and same-game parlays.
    Non-blocking; the frontend polls while the board builds."""
    date = request.args.get("date") or None
    try:
        import wnba
        data = wnba.board(date)
    except Exception as e:
        return jsonify({"error": f"wnba slate failed: {e}"}), 502
    if not data:
        return jsonify({"error": "simulating the slate in the background — retry shortly"}), 502
    return jsonify(data)


@app.route("/api/nba/slate")
def api_nba_slate():
    """NBA daily slate (possession engine; lights up when the season tips off)."""
    date = request.args.get("date") or None
    try:
        import basket
        data = basket.board("nba", date)
    except Exception as e:
        return jsonify({"error": f"nba slate failed: {e}"}), 502
    if not data:
        return jsonify({"error": "simulating the slate in the background — retry shortly"}), 502
    return jsonify(data)


@app.route("/api/nhl/slate")
def api_nhl_slate():
    """NHL daily slate (shot-event engine; lights up when the season starts)."""
    date = request.args.get("date") or None
    try:
        import hockey
        data = hockey.board(date)
    except Exception as e:
        return jsonify({"error": f"nhl slate failed: {e}"}), 502
    if not data:
        return jsonify({"error": "simulating the slate in the background — retry shortly"}), 502
    return jsonify(data)


@app.route("/api/nfl/slate")
def api_nfl_slate():
    """Drive-engine NFL slate: per-game win probs vs live Kalshi moneylines
    (edges), score/total/spread distributions, correlated player props and a
    default same-game parlay per game. Non-blocking; the frontend polls."""
    try:
        week = int(request.args.get("week", 1))
    except ValueError:
        week = 1
    week = max(1, min(18, week))
    try:
        import nfl_game_sim
        data = nfl_game_sim.board(week=week)
    except Exception as e:
        return jsonify({"error": f"nfl slate failed: {e}"}), 502
    if not data:
        return jsonify({"error": "simulating the slate in the background — retry shortly"}), 502
    return jsonify(data)


@app.route("/api/nfl/sim")
def api_nfl_sim():
    """Correlated per-game Monte Carlo seeded by Sleeper weekly projections:
    per-player floor/median/ceiling/boom (sim + best-ball), QB->WR stacks, and
    same-game-correlated prop over/unders (Pick 6). Non-blocking; polls."""
    try:
        week = int(request.args.get("week", 1))
    except ValueError:
        week = 1
    week = max(1, min(18, week))
    try:
        import nfl_dfs_sim
        data = nfl_dfs_sim.board(week=week)
    except Exception as e:
        return jsonify({"error": f"nfl sim failed: {e}"}), 502
    if not data:
        return jsonify({"error": "simulating the week in the background — retry shortly"}), 502
    return jsonify(data)


@app.route("/api/tennis")
def api_tennis():
    """Tennis match board: per-match model win% vs the de-vig Kalshi price (with a
    confidence blend), the coherent derived markets (total games / sets / aces),
    and plain-English angles. Non-blocking: 202 while the first board computes."""
    try:
        import tennis_prices
        board = tennis_prices.board()
    except Exception as e:
        return jsonify({"error": f"tennis sim failed: {e}"}), 502
    if not board:
        return jsonify({"status": "computing",
                        "message": "rating players from their charted matches…"}), 202
    # Live merge happens per-request (60s ESPN snapshot), NOT inside the 20-min
    # board cache: scores move fast and the upset radar is only useful live.
    try:
        import tennis_live
        board = tennis_live.attach(board)
    except Exception:
        pass
    return jsonify(board)


@app.route("/api/golf")
def api_golf():
    """Golf tournament simulator: win / top-5/10/20 / make-the-cut and every
    head-to-head from a strokes model (season scoring average + course length +
    wind/temp/altitude), simulating the remaining rounds through the cut and
    priced vs Kalshi (make-cut, H2H). Non-blocking: 202 while the board warms."""
    try:
        import golf
        b = golf.board(request.args.get("tour", "pga"))
    except Exception as e:
        return jsonify({"error": f"golf sim failed: {e}"}), 502
    if not b:
        return jsonify({"status": "computing",
                        "message": "simulating the field through the cut…"}), 202
    return jsonify(b)


@app.route("/api/model-trust")
def api_model_trust():
    """How far each sport's model is allowed to depart from the market price, and
    why — the blend weight fitted on point-in-time backtests, the sample behind
    it, and our model's log-loss against the market's. Sports with no measurement
    show the cautious default."""
    try:
        import model_trust
        return jsonify(model_trust.report())
    except Exception as e:
        return jsonify({"error": f"model trust unavailable: {e}"}), 502


@app.route("/api/sim/status")
def api_sim_status():
    """Freshness + run state of each cached season sim."""
    import deep_cache
    return jsonify({k: deep_cache.status(k) for k in ("mlb_deep", "f1", "nascar")})


@app.route("/api/sim/rerun", methods=["POST"])
def api_sim_rerun():
    """Force a fresh run of one cached season sim (manual weekly-style refresh)."""
    import deep_cache
    key = {"mlb": "mlb_deep", "f1": "f1", "nascar": "nascar",
           "nfl": "nfl", "nba": "nba", "nhl": "nhl"}.get(
        (request.args.get("sport") or "").lower())
    if not key:
        return jsonify({"error": "unknown sport"}), 400
    started = deep_cache.run_job(key, force=True)
    return jsonify({"started": started, "status": deep_cache.status(key)})


# Latest completed deep-season run, kept in-process. {agg, season}.
_deep = {"agg": None, "season": None}


@app.route("/api/baseball/futures/deep", methods=["POST"])
def api_baseball_deep_start():
    """Kick off the deep multicore season run in the background (it takes minutes).
    Poll /deep/status; when ready, GET /futures?engine=deep for the deep board."""
    import datetime as _dt
    import deep_season
    import deep_cache
    if deep_season.PROGRESS.get("running"):
        return jsonify({"started": False, "already_running": True,
                        "progress": deep_season.PROGRESS})
    # Force a fresh run; result is persisted to disk and reused for everyone.
    started = deep_cache.run_job("mlb_deep", force=True)
    return jsonify({"started": started, "season": str(clock.today_et().year)})


@app.route("/api/baseball/live/<int:game_pk>")
def api_baseball_live(game_pk):
    """Live-game feedback: pitcher pitch counts + lines, per-hitter AB results, and
    our model's hit odds (incl. conditional 2nd-hit + live remaining-AB odds)."""
    try:
        return jsonify(baseball.live_game_feedback(game_pk))
    except Exception as e:
        return jsonify({"error": f"live feed failed: {e}"}), 502


@app.route("/api/baseball/team")
def api_baseball_team():
    """Per-player simulated season stat lines for one team from the latest deep
    run (hits/HR/BB/K/R for bats, IP/K/BB/ERA for arms). Needs ?abbr=NYY."""
    import datetime as _dt
    import deep_season
    season = request.args.get("season") or str(clock.today_et().year)
    abbr = (request.args.get("abbr") or "").upper()
    agg = _deep.get("agg")
    if not agg or _deep.get("season") != season:
        return jsonify({"error": "Run the deep simulation first to get player lines."}), 409
    try:
        amap = baseball._abbr_map(season)            # tid -> abbr
        tid = next((t for t, a in amap.items() if a == abbr), None)
        if tid is None:
            return jsonify({"error": f"unknown team {abbr}"}), 404
        return jsonify(deep_season.team_detail(agg, season, tid))
    except Exception as e:
        return jsonify({"error": f"team detail failed: {e}"}), 502


@app.route("/api/baseball/futures/deep/status")
def api_baseball_deep_status():
    import datetime as _dt
    import deep_season
    season = request.args.get("season") or str(clock.today_et().year)
    p = dict(deep_season.PROGRESS)
    if p.get("running") and p.get("started"):
        import time as _t
        done, total = p.get("done", 0), p.get("total", 1) or 1
        elapsed = _t.time() - p["started"]
        p["pct"] = round(100 * done / total, 1)
        p["eta_sec"] = round(elapsed / done * (total - done)) if done else None
    p["ready"] = bool(_deep.get("agg") and _deep.get("season") == season)
    import deep_cache
    p["age_sec"] = deep_cache.age("mlb_deep")
    import deep_history
    p["history_dates"] = deep_history.dates()[:60]
    return jsonify(p)


@app.route("/api/baseball/futures/deep/history")
def api_baseball_deep_history():
    """What changed between two nightly deep runs, and what each change was worth.

    ?date=YYYY-MM-DD picks a day (default: the newest run). Every day describes
    the move from the PREVIOUS stored run to itself, so the current day is
    answerable as soon as its run lands."""
    import deep_history
    date = request.args.get("date") or None
    try:
        rep = deep_history.report(date)
    except Exception as e:
        return jsonify({"error": f"history failed: {e}"}), 502
    if not rep:
        return jsonify({"empty": True, "dates": deep_history.dates(),
                        "message": "No deep-run history yet. It starts building "
                                   "from the next nightly run."})
    return jsonify(rep)


@app.route("/api/baseball/futures/deep/history/export")
def api_baseball_deep_history_export():
    """The whole stored history as plain JSON, for the nightly Action to commit
    to the repo.

    This is a PULL: the app holds no GitHub credentials and never writes there.
    The scheduled workflow fetches this, writes it under history/, and commits
    with its own token -- the same direction the weekly sim-rerun workflow
    already runs in."""
    import deep_history
    try:
        return jsonify(deep_history.export_bundle())
    except Exception as e:
        return jsonify({"error": f"export failed: {e}"}), 502


@app.route("/api/baseball/futures/deep/history/restore", methods=["POST"])
def api_baseball_deep_history_restore():
    """Pull the repo copy back into this host's cache. Runs automatically on boot;
    this endpoint is for forcing it after a manual snapshot."""
    import deep_history
    try:
        return jsonify(deep_history.restore_from_github(
            overwrite=request.args.get("overwrite") in ("1", "true", "yes")))
    except Exception as e:
        return jsonify({"error": f"restore failed: {e}"}), 502


@app.route("/api/bestbets")
def api_bestbets():
    """Cross-sport Best Bets: every positive net-of-fee edge across all our
    models (MLB slate/props, season futures, UFC, tennis, crypto, arbitrage),
    ranked by the edge you'd actually bank. Sources degrade independently."""
    import datetime as _dt
    import bestbets
    date = request.args.get("date") or clock.today_et().isoformat()
    try:
        sims = tiers.cap_sims(_tier(), request.args.get("sims", 2500))
    except ValueError:
        return jsonify({"error": "bad params"}), 400
    try:
        return jsonify(bestbets.board(date, date[:4], sims=sims))
    except Exception as e:
        return jsonify({"error": f"best bets failed: {e}"}), 502


@app.route("/api/baseball/edges")
def api_baseball_edges():
    """Rank the biggest model/sim-vs-Kalshi disparities across the day's slate.
    For each leg we can price live, edge = our simulated probability minus
    Kalshi's implied price. A disagreement finder, flagged by confidence."""
    import datetime as _dt
    date = request.args.get("date") or clock.today_et().isoformat()
    season = request.args.get("season") or date[:4]
    try:
        min_edge = float(request.args.get("min_edge", 4))
        sims = tiers.cap_sims(_tier(), request.args.get("sims", 4000))
    except ValueError:
        return jsonify({"error": "bad params"}), 400
    try:
        games = baseball.analyze_slate(date, season)
    except Exception as e:
        return jsonify({"error": f"baseball data failed: {e}"}), 502
    res = baseball.find_edges(games, n_sims=sims, min_edge=min_edge, types=_prop_types())
    res["date"] = date
    return jsonify(res)


@app.route("/api/baseball/pick6")
def api_baseball_pick6():
    """DraftKings Pick 6 board: our player-prop projections framed as More/Less at
    DK-style lines, from the same shared game sim the combos use."""
    import datetime as _dt
    date = request.args.get("date") or clock.today_et().isoformat()
    season = request.args.get("season") or date[:4]
    try:
        games = baseball.analyze_slate(date, season)
    except Exception as e:
        return jsonify({"error": f"baseball data failed: {e}"}), 502
    return jsonify(baseball.pick6_board(games))


@app.route("/api/baseball/pick6/sheet")
def api_baseball_pick6_sheet():
    """Pick 6 game browser: one game's full per-player simulated stat sheet
    (avg + More% at every line) plus the slate's game list. ?date=&pk=."""
    import datetime as _dt
    date = request.args.get("date") or clock.today_et().isoformat()
    season = request.args.get("season") or date[:4]
    try:
        pk = int(request.args.get("pk", 0)) or None
    except ValueError:
        pk = None
    try:
        games = baseball.analyze_slate(date, season)
    except Exception as e:
        return jsonify({"error": f"baseball data failed: {e}"}), 502
    return jsonify(baseball.pick6_game_sheet(games, pk))


@app.route("/api/baseball/pick6/eval", methods=["POST"])
def api_baseball_pick6_eval():
    """Exact joint odds for a hand-built same-game Pick 6 slip (masks ANDed on
    the shared sim, not an independence product)."""
    import datetime as _dt
    d = request.get_json(force=True, silent=True) or {}
    date = d.get("date") or clock.today_et().isoformat()
    legs = d.get("legs") or []
    try:
        pk = int(d.get("pk", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "bad pk"}), 400
    if not (1 <= len(legs) <= 6):
        return jsonify({"error": "1-6 legs"}), 400
    try:
        games = baseball.analyze_slate(date, date[:4])
    except Exception as e:
        return jsonify({"error": f"baseball data failed: {e}"}), 502
    return jsonify(baseball.pick6_eval(games, pk, legs))


@app.route("/api/baseball/sgp")
def api_baseball_sgp():
    """Same-game parlays with correlation-aware (simulated) joint odds. Legs from
    one game are correlated, so these are read off a full game simulation rather
    than multiplying independent marginals."""
    import datetime as _dt
    locked = _locked("same_game_parlay")
    if locked:
        return locked
    date = request.args.get("date") or clock.today_et().isoformat()
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
    res = baseball.build_same_game_parlays(games, n_legs=legs, target_pct=target, types=_prop_types(),
                                           target_payout=payout, n_sims=sims,
                                           max_legs=tiers.cap_legs(_tier(), 30),
                                           allow_live=_allow_live())
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
    date = request.args.get("date") or clock.today_et().isoformat()
    season = request.args.get("season") or date[:4]
    try:
        legs = tiers.cap_legs(_tier(), request.args.get("legs", 4))
        target = float(request.args.get("target", 55))
        payout = request.args.get("payout")
        payout = float(payout) if payout not in (None, "", "0") else 0
        sims = tiers.cap_sims(_tier(), request.args.get("sims", 5000))
        max_total = tiers.cap_legs(_tier(), 30)   # tier ceiling is the only cap
    except ValueError:
        return jsonify({"error": "bad legs/payout"}), 400
    # same_game off -> one leg per game (a plain cross-game combo, still simulated
    # so every leg shows model vs sim); on -> may stack correlated legs in a game.
    same_game = request.args.get("same_game", "1") != "0"
    # The leg-count and payout targets are each "require" (hard) / "prefer"
    # (recommendation) / "off", combined by conn ('and'/'or').
    modes = ("require", "prefer", "off")
    legs_mode = request.args.get("legs_mode", "prefer")
    payout_mode = request.args.get("payout_mode", "off")
    conn = "and" if request.args.get("conn") == "and" else "or"
    if legs_mode not in modes:
        legs_mode = "prefer"
    if payout_mode not in modes:
        payout_mode = "off"
    include_live = request.args.get("include_live") == "1"
    # Optional game grid: "sel" = comma list of "pk" (whole game) or "pk:Team"
    # (only that team's legs). Empty -> all games.
    sel = {}
    for tok in (request.args.get("sel") or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        pk, _, team = tok.partition(":")
        try:
            sel[int(pk)] = team or True
        except ValueError:
            pass
    try:
        games = baseball.analyze_slate(date, season)
    except Exception as e:
        return jsonify({"error": f"baseball data failed: {e}"}), 502
    item = baseball.build_mixed_parlay(games, n_legs=legs, target_pct=target,
                                       target_payout=payout, n_sims=sims,
                                       max_legs_per_game=max_total if same_game else 1,
                                       max_total_legs=max_total,
                                       legs_mode=legs_mode, payout_mode=payout_mode,
                                       conn=conn, game_sel=sel or None,
                                       include_live=include_live, types=_prop_types())
    return jsonify({"parlay": item})


@app.route("/api/baseball/value")
def api_baseball_value():
    """Kalshi player-prop prices vs each batter's recent game-log rate -> value plays."""
    locked = _locked("racing_picks")   # gated with the other edge-finder tools (Pro)
    if locked:
        return locked
    import value
    import datetime as _dt
    season = request.args.get("season") or clock.today_et().isoformat()[:4]
    try:
        n = max(5, min(40, int(request.args.get("games", 15))))
        edge = max(3.0, float(request.args.get("edge", 10)))
    except ValueError:
        return jsonify({"error": "bad params"}), 400
    try:
        return jsonify(value.find_value(season=season, n_games=n, min_edge=edge))
    except Exception as e:
        return jsonify({"error": f"value scan failed: {e}"}), 502


@app.route("/api/baseball/record")
def api_baseball_record():
    try:
        baseball.grade_picks()
    except Exception:
        pass
    return jsonify(store.mlb_record())


@app.route("/api/baseball/proplog")
def api_baseball_proplog():
    """Aggregate accuracy of the prop model, recent form, and Kalshi's own price,
    from the background recorder's logged + graded batter props."""
    import mlb_recorder
    try:
        edge = max(3.0, float(request.args.get("edge", 8)))
    except ValueError:
        return jsonify({"error": "bad params"}), 400
    try:
        mlb_recorder.grade_due()   # grade any games that just went final
    except Exception:
        pass
    report = store.prop_report(min_edge=edge)
    report["recorder"] = mlb_recorder.status()
    return jsonify(report)


@app.route("/api/baseball/hits")
def api_baseball_hits():
    """Predicted Hits + Risky Hits: from the recorder's graded props, the ones the
    model liked that cashed, and the longshots that would've paid big."""
    import mlb_recorder
    import datetime as _dt
    date = request.args.get("date")
    if date == "today":
        date = clock.today_et().isoformat()
    try:
        mlb_recorder.grade_due()   # grade any games that just went final
    except Exception:
        pass
    res = store.prop_hit_combos(date=date)
    res["recorder"] = mlb_recorder.status()
    return jsonify(res)


@app.route("/api/stats")
def api_stats():
    return jsonify(store.stats())


if __name__ == "__main__":
    import os
    import argparse
    # Works the same in any shell:  python app.py --debug --port 8080
    parser = argparse.ArgumentParser(description="Vigil server")
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
