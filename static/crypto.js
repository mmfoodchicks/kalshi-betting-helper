// The crypto terminal (v1 2026-11-27, v2 the same day; crypto_terminal.py). Kalshi's 15-minute and hourly crypto
// contracts priced off the rule each settles on, a zoomable chart, the momentum and "what if" panels, Kalshi's own
// price history for the open market, and PAPER positions. Display and paper only: this script reads /api/crypto/*,
// writes only to /api/crypto/paper/* (a row in Vigil's own file), and opens one public market-data WebSocket
// (Coinbase's ticker feed) in THIS browser while the tab is on screen -- the server holds no stream. Every server
// string is escaped (h); no inline handlers; one refresh timer, re-armed only while the tab is on screen and the
// page visible. Times show in Mountain time, the owner's clock.
(function () {
  "use strict";
  var GRANS = [[15, "15s"], [30, "30s"], [60, "1m"], [300, "5m"], [900, "15m"], [3600, "1h"]];
  var LIVE_ONLY = {15: true, 30: true};          // Coinbase's REST candles start at one minute
  var REFRESH_MS = 15000;
  var MAXBARS = 480;                             // every bar buffer is capped (two hours of 15 s bars)
  var LIB = "/static/vendor/lightweight-charts-4.1.3.standalone.production.js";
  var WS_URL = "wss://ws-feed.exchange.coinbase.com";
  var st = {coin: null, gran: 60, timer: null, busy: false, wired: false, board: null, research: null, rec: null,
            bars: {}, hist: [], ws: null, wsSym: null, lastTick: 0, lastPx: null, polls: 0, fair: {ticker: null, pts: []},
            paper: null, show: {ema: true, vwap: true, bb: false, sr: true, band: true}, raf: false, lib: false,
            charts: null, contract: null};

  function h(x) {
    return String(x == null ? "" : x).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  function el(id) { return document.getElementById(id); }
  function onScreen() {
    var t = el("tab-crypto");
    return !!t && !t.classList.contains("hidden") && !document.hidden;
  }
  function num(x, d) { return x == null || !isFinite(x) ? "–" : Number(x).toFixed(d); }
  function px(x) {
    if (x == null || !isFinite(x)) return "–";
    return Math.abs(x) >= 100 ? Number(x).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2})
      : Number(x).toPrecision(6);
  }
  function mtFmt(ts, sec) {
    try {
      return new Date(ts * 1000).toLocaleTimeString("en-US", sec ? {timeZone: "America/Denver", hour: "numeric",
        minute: "2-digit", second: "2-digit"} : {timeZone: "America/Denver", hour: "numeric", minute: "2-digit"});
    } catch (e) {
      return new Date(ts * 1000).toISOString().slice(11, sec ? 19 : 16);
    }
  }
  function mt(ts) { return ts ? mtFmt(ts) + " MT" : ""; }
  function left(s) {
    if (s == null) return "";
    if (s <= 0) return "closed";
    var m = Math.floor(s / 60), r = Math.round(s % 60);
    return m >= 60 ? Math.floor(m / 60) + "h " + (m % 60) + "m" : m + "m " + r + "s";
  }
  function pct(p) { return p == null ? "–" : (100 * p).toFixed(1) + "%"; }
  function css(v, dflt) {
    var x = getComputedStyle(document.documentElement).getPropertyValue(v);
    return (x && x.trim()) || dflt;
  }

  function arm() {
    if (st.timer) { clearTimeout(st.timer); st.timer = null; }
    if (!onScreen()) { closeWS(); return; }    // a hidden tab or a backgrounded phone is never polled or streamed
    st.timer = setTimeout(function () { st.timer = null; if (onScreen()) load(); else closeWS(); }, REFRESH_MS);
  }

  function getJSON(url) {
    return fetch(url).then(function (r) { return r.json(); });
  }
  function postJSON(url, body) {                 // the paper ledger only: Vigil's own file, never an exchange
    if (url.indexOf("/api/crypto/paper/") !== 0) return Promise.reject(new Error("paper routes only"));
    return fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})
      .then(function (r) { return r.json(); });
  }

  // ---- the live feed: Coinbase's public ticker in this browser, bars built here, capped ----
  function openWS(sym) {
    if (st.ws && st.wsSym === sym) return;
    closeWS();
    if (!window.WebSocket) return;
    try {
      var ws = new WebSocket(WS_URL);
      st.ws = ws;
      st.wsSym = sym;
      ws.onopen = function () {
        ws.send(JSON.stringify({type: "subscribe", product_ids: [sym], channels: ["ticker"]}));
      };
      ws.onmessage = function (ev) {
        if (!onScreen()) { closeWS(); return; }
        var m;
        try { m = JSON.parse(ev.data); } catch (e) { return; }
        if (!m || m.type !== "ticker" || m.product_id !== st.wsSym) return;
        var p = Number(m.price), t = Date.parse(m.time) / 1000, v = Number(m.last_size) || 0;
        if (!isFinite(p) || !isFinite(t)) return;
        st.lastTick = t;
        st.lastPx = p;
        st.book = {bid: Number(m.best_bid), ask: Number(m.best_ask), bs: Number(m.best_bid_size), as: Number(m.best_ask_size)};
        [15, 30, 60, 300, 900, 3600].forEach(function (g) { addTick(g, t, p, v); });
        schedule();
      };
      ws.onclose = function () { if (st.ws === ws) { st.ws = null; st.wsSym = null; } };
      ws.onerror = function () { try { ws.close(); } catch (e) { /* closing a failed socket */ } };
    } catch (e) {
      st.ws = null;
    }
  }
  function closeWS() {
    if (st.ws) {
      try { st.ws.close(); } catch (e) { /* already closed */ }
    }
    st.ws = null;
    st.wsSym = null;
  }
  function addTick(g, t, p, v) {
    var b = st.bars[g] || (st.bars[g] = []);
    var bt = Math.floor(t / g) * g;
    var last = b[b.length - 1];
    if (last && last[0] === bt) {
      last[2] = Math.max(last[2], p); last[3] = Math.min(last[3], p); last[4] = p; last[5] += v;
    } else if (!last || bt > last[0]) {
      b.push([bt, p, p, p, p, v]);
      if (b.length > MAXBARS) b.splice(0, b.length - MAXBARS);
    }
  }
  function schedule() {
    if (st.raf) return;
    st.raf = true;
    requestAnimationFrame(function () { st.raf = false; drawPrice(); momentum(); });
  }

  // ---- indicators over a capped bar array (O(bars)) ----
  function ema(vals, n) {
    var a = 2 / (n + 1), out = [], e = null;
    vals.forEach(function (v) { e = e == null ? v : e + a * (v - e); out.push(e); });
    return out;
  }
  function rsi(cl, n) {
    var g = 0, l = 0, out = [];
    for (var i = 0; i < cl.length; i++) {
      var d = i ? cl[i] - cl[i - 1] : 0;
      g += (Math.max(d, 0) - g) / n; l += (Math.max(-d, 0) - l) / n;
      out.push(g + l > 0 ? 100 * g / (g + l) : 50);
    }
    return out;
  }
  function series(bars) {
    var cl = bars.map(function (b) { return b[4]; });
    var e5 = ema(cl, 5), e9 = ema(cl, 9), e21 = ema(cl, 21), e12 = ema(cl, 12), e26 = ema(cl, 26);
    var macd = e12.map(function (v, i) { return v - e26[i]; }), sig = ema(macd, 9);
    var pv = 0, vv = 0, vwap = [], up = [], lo = [];
    bars.forEach(function (b, i) {
      var tp = (b[2] + b[3] + b[4]) / 3;
      pv += tp * b[5]; vv += b[5];
      vwap.push(vv > 0 ? pv / vv : tp);
      var w = cl.slice(Math.max(0, i - 19), i + 1), m = w.reduce(function (a, x) { return a + x; }, 0) / w.length;
      var sd = Math.sqrt(w.reduce(function (a, x) { return a + (x - m) * (x - m); }, 0) / w.length);
      up.push(m + 2 * sd); lo.push(m - 2 * sd);
    });
    return {cl: cl, e5: e5, e9: e9, e21: e21, hist: macd.map(function (v, i) { return v - sig[i]; }), rsi: rsi(cl, 14),
            vwap: vwap, up: up, lo: lo};
  }

  // ---- the charts (TradingView Lightweight Charts, vendored, loaded with the tab) ----
  function loadLib(cb) {
    if (window.LightweightCharts) { st.lib = true; cb(); return; }
    var s = document.createElement("script");
    s.src = LIB;
    s.onload = function () { st.lib = true; cb(); };
    s.onerror = function () { el("cxChart").textContent = "Could not load the chart library."; };
    document.head.appendChild(s);
  }
  function mkChart(box, height) {
    var LC = window.LightweightCharts;
    return LC.createChart(box, {
      width: Math.max(280, box.clientWidth || 360), height: height,
      layout: {background: {type: "solid", color: "transparent"}, textColor: css("--muted", "#95a1b3"), fontSize: 11},
      grid: {vertLines: {color: "rgba(255,255,255,0.04)"}, horzLines: {color: "rgba(255,255,255,0.04)"}},
      crosshair: {mode: LC.CrosshairMode.Normal},
      rightPriceScale: {borderColor: "rgba(255,255,255,0.1)"},
      timeScale: {borderColor: "rgba(255,255,255,0.1)", timeVisible: true, secondsVisible: true, rightOffset: 4,
                  tickMarkFormatter: function (t) { return mtFmt(t); }},
      localization: {timeFormatter: function (t) { return mtFmt(t, true) + " MT"; }},
      handleScroll: true, handleScale: true
    });
  }
  function buildCharts() {
    if (st.charts) return;
    var c = mkChart(el("cxChart"), 300), k = mkChart(el("cxContract"), 200);
    var line = function (ch, col, w, style) {
      return ch.addLineSeries({color: col, lineWidth: w || 1, lineStyle: style || 0, priceLineVisible: false,
                               lastValueVisible: false, crosshairMarkerVisible: false});
    };
    var yes = css("--yes", "#34d399"), no = css("--no", "#f87171"), acc = css("--accent", "#5b8cff"),
        hold = css("--hold", "#fbbf24"), faint = css("--faint", "#5d6678");
    st.charts = {
      price: c, contract: k,
      candles: c.addCandlestickSeries({upColor: yes, downColor: no, wickUpColor: yes, wickDownColor: no, borderVisible: false}),
      vol: c.addHistogramSeries({priceFormat: {type: "volume"}, priceScaleId: "vol", color: "rgba(149,161,179,0.35)",
                                 lastValueVisible: false, priceLineVisible: false}),
      e5: line(c, "#c084fc"), e9: line(c, "#22d3ee"), e21: line(c, hold), vwap: line(c, "#e8edf4", 1, 1),
      bbu: line(c, faint, 1, 2), bbl: line(c, faint, 1, 2), bandu: line(c, acc, 2, 2), bandl: line(c, acc, 2, 2),
      lines: [],
      kya: line(k, yes, 2), kyb: line(k, yes, 1, 2), kna: line(k, no, 2), knb: line(k, no, 1, 2), kfair: line(k, acc, 2)
    };
    c.priceScale("vol").applyOptions({scaleMargins: {top: 0.82, bottom: 0}});
    window.addEventListener("resize", function () {
      if (!st.charts) return;
      st.charts.price.applyOptions({width: Math.max(280, el("cxChart").clientWidth || 360)});
      st.charts.contract.applyOptions({width: Math.max(280, el("cxContract").clientWidth || 360)});
    });
  }

  function barsNow() {
    var g = st.gran, live = st.bars[g] || [];
    if (LIVE_ONLY[g]) return live.slice();
    var base = st.hist.slice();
    var last = base.length ? base[base.length - 1][0] : 0;
    live.forEach(function (b) {                      // the live feed refreshes the newest bars only
      if (b[0] > last) base.push(b.slice());
      else if (b[0] === last) { var x = base[base.length - 1]; x[2] = Math.max(x[2], b[2]); x[3] = Math.min(x[3], b[3]); x[4] = b[4]; }
    });
    return base.slice(-MAXBARS);
  }

  function drawPrice() {
    if (!st.charts) return;
    var C = st.charts, bars = barsNow(), d = st.board || {};
    var note = el("cxChartNote");
    if (!bars.length) {
      C.candles.setData([]); C.vol.setData([]);
      if (note) note.textContent = LIVE_ONLY[st.gran] ? "Building " + st.gran + " s bars from the live feed since the tab " +
        "opened (Coinbase keeps no sub-minute history)." : "No candles.";
      return;
    }
    var s = series(bars);
    C.candles.setData(bars.map(function (b) { return {time: b[0], open: b[1], high: b[2], low: b[3], close: b[4]}; }));
    C.vol.setData(bars.map(function (b) { return {time: b[0], value: b[5], color: b[4] >= b[1] ? "rgba(52,211,153,0.3)" : "rgba(248,113,113,0.3)"}; }));
    var ln = function (arr) { return bars.map(function (b, i) { return {time: b[0], value: arr[i]}; }); };
    C.e5.setData(st.show.ema ? ln(s.e5) : []); C.e9.setData(st.show.ema ? ln(s.e9) : []); C.e21.setData(st.show.ema ? ln(s.e21) : []);
    C.vwap.setData(st.show.vwap ? ln(s.vwap) : []);
    C.bbu.setData(st.show.bb ? ln(s.up).slice(19) : []); C.bbl.setData(st.show.bb ? ln(s.lo).slice(19) : []);
    C.lines.forEach(function (l) { C.candles.removePriceLine(l); });
    C.lines = [];
    var pl = function (p, col, title, style) {
      if (p == null || !isFinite(p)) return;
      C.lines.push(C.candles.createPriceLine({price: p, color: col, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title: title}));
    };
    if (d.m15 && d.m15.rule) pl(d.m15.rule.strike, css("--hold", "#fbbf24"), "15m strike", 2);
    if (d.proxy && d.proxy.price) pl(d.proxy.price, css("--accent", "#5b8cff"), "proxy", 1);
    if (st.show.sr) {
      var w = bars.slice(-60), hi = Math.max.apply(null, w.map(function (b) { return b[2]; })),
          lo = Math.min.apply(null, w.map(function (b) { return b[3]; }));
      pl(hi, "rgba(232,237,244,0.35)", "60-bar high", 3); pl(lo, "rgba(232,237,244,0.35)", "60-bar low", 3);
    }
    // the FORECAST band: the random walk's 95% range from now to the 15-minute close -- dashed and coloured
    // apart from everything observed; it has no direction because no tested indicator earned one
    var band = [[], []], m = d.m15, lv = st.lastPx || (d.proxy || {}).price, sg = d.sigma_s;
    if (st.show.band && m && m.close_ts && lv && sg) {
      var t0 = bars[bars.length - 1][0], step = Math.max(st.gran, 15);
      for (var t = t0; t <= Math.max(t0, m.close_ts); t += step) {
        var sd = sg * Math.sqrt(Math.max(0, t - t0) + 1e-9);
        band[0].push({time: t, value: lv * Math.exp(1.96 * sd)}); band[1].push({time: t, value: lv * Math.exp(-1.96 * sd)});
      }
    }
    C.bandu.setData(band[0]); C.bandl.setData(band[1]);
    C.candles.setMarkers(markers(bars, s));
    if (note) {
      note.textContent = (LIVE_ONLY[st.gran] ? "Live " + st.gran + " s bars from Coinbase's public feed in this browser. " :
        "Coinbase candles, the newest refreshed live. ") + "Dashed blue: the random walk's 95% band to the 15-minute close " +
        "(a forecast, not a direction). Arrows: past paper signals (logged before the close). Circles: OBSERVED EMA 9/21 crosses. " +
        "Squares: POSSIBLE reversals, the 5-minute model at 60%+ (held-out: said 62%, hit 59%).";
    }
  }

  function markers(bars, s) {
    var out = [], t0 = bars[0][0], g = st.gran;
    var sig = ((((st.rec || {}).extra || {}).signals || {})[st.coin]) || [];
    sig.forEach(function (x) {
      if (!x.side || x.ts < t0) return;
      var hit = x.result == null || x.result < 0 ? "" : ((x.side === "yes") === (x.result === 1) ? " ✓" : " ✗");
      out.push({time: Math.floor(x.ts / g) * g, position: x.side === "yes" ? "belowBar" : "aboveBar",
                color: x.side === "yes" ? css("--yes", "#34d399") : css("--no", "#f87171"),
                shape: x.side === "yes" ? "arrowUp" : "arrowDown", text: "paper " + x.side.toUpperCase() + hit});
    });
    for (var i = 1; i < bars.length; i++) {
      var a = s.e9[i - 1] - s.e21[i - 1], b = s.e9[i] - s.e21[i];
      if (i > 21 && a * b < 0) {
        out.push({time: bars[i][0], position: "inBar", color: css("--hold", "#fbbf24"), shape: "circle",
                  text: b > 0 ? "EMA up-cross" : "EMA down-cross"});
      }
    }
    (((st.board || {}).direction || {}).marks || []).forEach(function (x) {
      if (x[0] < t0) return;
      out.push({time: Math.floor(x[0] / g) * g, position: x[2] > 0 ? "aboveBar" : "belowBar", color: css("--accent", "#5b8cff"),
                shape: "square", text: "possible reversal " + Math.round(100 * x[1]) + "% (5 min, model)"});
    });
    var seen = {}, have = {};
    bars.forEach(function (b) { have[b[0]] = 1; });
    out = out.filter(function (x) { return have[x.time]; });
    return out.sort(function (x, y) { return x.time - y.time; }).filter(function (x) {
      var k = x.time + x.text;
      if (seen[k]) return false;
      seen[k] = 1;
      return true;
    });
  }

  // ---- panels ----
  function honesty() {
    var r = (st.rec || {}).record || {}, p = r.paper || {};
    if (r.same_rows_model_logloss == null) return '<p class="sub" style="margin:4px 0">The live record has no graded rows yet.</p>';
    var losing = r.same_rows_model_logloss > r.same_rows_kalshi_logloss;
    return '<div class="card" style="margin:8px 0;padding:8px 12px;border-color:' + (losing ? "var(--no)" : "var(--yes)") + '">' +
      "<b>" + (losing ? "The settlement model is LOSING to Kalshi's price on the live record" :
                        "The settlement model is ahead of Kalshi's price on the live record") + "</b>" +
      '<div class="stats">log loss ' + num(r.same_rows_model_logloss, 4) + " vs Kalshi " + num(r.same_rows_kalshi_logloss, 4) +
      " over " + h(r.priced) + " rows" + (r.decided ? "" : " (not yet decided past 2 SE)") + " · paper rule " + h(p.bets || 0) +
      " bets at " + (p.per_bet_c == null ? "–" : (p.per_bet_c > 0 ? "+" : "") + num(p.per_bet_c, 2) + "c ± " + num(p.se_per_bet_c, 2)) +
      ". Every signal below is unproven until this changes.</div></div>";
  }

  function edgeCell(e, side, proven) {
    if (!e || e[side] == null) return "–";
    var v = e[side], cls = v > 0 ? "color:var(--yes)" : "color:var(--muted)";
    return '<span style="' + cls + '" title="' + h((proven ? "" : "unproven: ") + "model chance x 100 minus the ask minus " +
      "Kalshi's taker fee (" + num(e[side + "_fee"], 2) + "c)") + '">' + (v > 0 ? "+" : "") + num(v, 1) + "c</span>";
  }
  function ivCell(r) {
    if (r.implied_vol_ann_pct != null) return num(r.implied_vol_ann_pct, 0) + "%";
    return r.iv_note ? '<span title="' + h(r.iv_note) + '" style="color:var(--hold)">none</span>' : "–";
  }

  function m15Card(r, proven) {
    if (!r) return '<p class="sub">No open 15-minute market.</p>';
    var rule = r.rule;
    var says = rule ? h(rule.says) : "the settlement rule did not parse, so this market is not priced";
    var band = r.p_band ? " (" + pct(r.p_band[0]) + "–" + pct(r.p_band[1]) + " at 0.8x–1.25x the volatility)" : "";
    var sg = r.signal || {};
    return '<div class="card" style="margin:8px 0;padding:10px 12px">' +
      '<div class="row-between"><b>15 minutes: up or down</b><span class="sub">closes ' + h(mt(r.close_ts)) + " · " +
      h(left(r.tau_s)) + "</span></div>" +
      '<p class="sub" style="margin:4px 0">Settles on: ' + says + ".</p>" +
      (r.in_window ? '<p class="sub" style="margin:4px 0;color:var(--hold)">Inside the settlement window: the seconds ' +
        "already averaged are taken at the current level (an approximation; the record never logs here).</p>" : "") +
      '<div class="stats">Model <b>' + pct(r.p_model) + "</b>" + h(band) + " · Kalshi bid/ask <b>" + num(r.yes_bid, 1) +
      "/" + num(r.yes_ask, 1) + "c</b> (mid " + num(r.kalshi_mid, 1) + ") · implied vol <b>" + ivCell(r) + "</b></div>" +
      (r.iv_note ? '<p class="sub" style="margin:4px 0;color:var(--hold)">' + h(r.iv_note) + ".</p>" : "") +
      '<div class="stats">Edge at the ask, fee in: YES ' + edgeCell(r.edge, "yes", proven) + " · NO " +
      edgeCell(r.edge, "no", proven) + "</div>" +
      '<div class="stats">Signal: <b>' + h(sg.text || "–") + "</b>" + (proven ? "" : ' <span class="chip">unproven</span>') +
      "</div></div>";
  }

  function whatIf(r) {
    if (!r || !r.what_if || !r.what_if.length) return "";
    var rows = r.what_if.map(function (w) {
      return "<tr><td>" + (w.bp > 0 ? "+" : "") + h(w.bp) + ' bp</td><td style="text-align:right">' + (w.move > 0 ? "+" : "") +
        h(px(w.move)) + '</td><td style="text-align:right">' + h(px(w.level)) + '</td><td style="text-align:right"><b>' +
        num(100 * w.p, 1) + "c</b></td></tr>";
    }).join("");
    return '<div class="card" style="margin:8px 0;padding:10px 12px"><b>What if</b> <span class="sub">the 15-minute ' +
      "market's fair YES a minute from now if the level moves this much (the model read forward: it says how much a " +
      "move is worth, not which way it will go)</span>" +
      '<div style="overflow-x:auto"><table style="width:100%;font-size:0.85rem;font-variant-numeric:tabular-nums">' +
      '<tr style="color:var(--muted)"><th style="text-align:left">move</th><th style="text-align:right">$</th>' +
      '<th style="text-align:right">level</th><th style="text-align:right">fair YES</th></tr>' + rows + "</table></div>" +
      '<div class="stats">Chance the level <b>touches</b> the strike before the close: <b>' + pct(r.p_cross) +
      "</b> (a random walk's crossing probability; touching is not settling on the other side). Kalshi now: " +
      num(r.yes_bid, 1) + "/" + num(r.yes_ask, 1) + "c.</div></div>";
  }

  function momentum() {
    var box = el("cxMomentum"), d = st.board;
    if (!box || !d || d.off) return;
    var g = st.gran, bars = barsNow().slice(-200);               // the chart's own bars, stated by size
    var lv = st.lastPx || (d.proxy || {}).price, sg = d.sigma_s, m = d.m15;
    if (bars.length < 30 || !lv || !sg) { box.innerHTML = '<p class="sub">Momentum: waiting for data.</p>'; return; }
    var s = series(bars), n = bars.length - 1, s1 = lv * sg * Math.sqrt(g), gl = g < 60 ? g + "s" : g / 60 + "m";
    var z = (s.e9[n] - s.e21[n]) / s1, z3 = (s.e9[n - 3] - s.e21[n - 3]) / s1;
    var state = z > 0.5 ? "bullish" : z < -0.5 ? "bearish" : "neutral";
    var trend = Math.abs(z) > Math.abs(z3) ? "strengthening" : "weakening";
    var m5 = (s.cl[n] / s.cl[n - 5] - 1) / (sg * Math.sqrt(5 * g));
    var cross = null;
    for (var i = n; i > Math.max(22, n - 30); i--) {
      if ((s.e9[i - 1] - s.e21[i - 1]) * (s.e9[i] - s.e21[i]) < 0) { cross = {t: bars[i][0], up: s.e9[i] > s.e21[i]}; break; }
    }
    var R = (st.research || {}).direction || {};
    var D = (d.direction || {}).horizons || {};
    var rev = [1, 3, 5].map(function (hh) {
      var x = R[String(hh)] || {}, now = D[String(hh)] || {};
      var fit = num((x.gain_vs_rw_nats || 0) * 1000, 2) + " ± " + num((x.se_days || 0) * 1000, 2) + " millinats a minute";
      if (x.passed && now.p_up != null) {
        return "<li>" + hh + " min: up <b>" + pct(now.p_up) + "</b>" + (now.p_reversal == null ? "" :
          ", against the last 5 minutes <b>" + pct(now.p_reversal) + "</b>") + " (model; passed its held-out test, " +
          fit + " over the random walk: a 1-3 point tilt, mostly mean reversion)</li>";
      }
      return "<li>" + hh + " min: <b>50% (random walk)</b>; " + (x.passed ? "model unavailable right now" :
        "no indicator beat the random walk on the held-out fortnight (best " + h(x.chosen || "?") + ", " + fit + ")") +
        "; the trailing 5-minute move reversed " + pct(x.reversal_rate_heldout) + " of the time on held-out history</li>";
    }).join("") + "<li>What the tilt did NOT do: become a settlement edge (the drift test failed) or pay inside the " +
      "window (buying at the ask and selling at the bid lost " + num((((st.research || {}).intrawindow || {}).heldout || {}).per_trade_c, 2) +
      "c a trade on held-out quotes).</li>";
    var rng = [1, 3, 5].map(function (hh) {
      var w = 1.96 * sg * Math.sqrt(60 * hh);
      return hh + " min " + px(lv * Math.exp(-w)) + "–" + px(lv * Math.exp(w));
    }).join(" · ");
    var dist = m && m.rule ? lv - m.rule.strike : null;
    var age = st.lastTick ? Math.max(0, Date.now() / 1000 - st.lastTick) : null;
    var bk = st.book && st.book.bs + st.book.as > 0 ? (st.book.bs - st.book.as) / (st.book.bs + st.book.as) : null;
    box.innerHTML = '<div class="card" style="margin:8px 0;padding:10px 12px"><b>Momentum and reversal</b> ' +
      '<span class="sub">description of what HAS happened; forecasts only where a test passed</span>' +
      '<div class="stats" style="margin:4px 0">State <b>' + h(state) + "</b> (EMA 9 vs 21 on the " + h(gl) + " bars: " + num(z, 2) +
      " bar SDs, " + h(trend) + ") · last 5 bars " + num(m5, 2) + " SDs · RSI " + num(s.rsi[n], 0) + " · MACD histogram " +
      (s.hist[n] >= 0 ? "positive" : "negative") + (bk == null ? "" : " · top-of-book imbalance " + num(bk, 2) + " (untested)") + "</div>" +
      '<div class="stats">Confirmed change (observed): ' + (cross ? "EMA 9 crossed " + (cross.up ? "above" : "below") +
        " EMA 21 at " + h(mt(cross.t)) : "none in the last 30 bars") + "</div>" +
      '<div class="stats">Possible reversal (a forecast): <ul style="margin:2px 0 2px 18px;padding:0">' + rev + "</ul></div>" +
      '<div class="stats">Expected 95% range (random walk): ' + h(rng) + "</div>" +
      '<div class="stats">Distance to the 15-minute strike: <b>' + (dist == null ? "–" : (dist > 0 ? "+" : "") + h(px(dist))) +
      "</b>" + (m && m.z != null ? " (" + num(m.z, 2) + " SDs of the move left)" : "") + " · time left " + h(left(m && m.tau_s)) +
      " · live feed " + (age == null ? "not connected" : num(age, 0) + " s old") + " · board " +
      num(Date.now() / 1000 - (d.ts || 0), 0) + " s old</div>" +
      '<p class="sub" style="margin:4px 0">The level here is Coinbase alone (the live feed) or the four-exchange median ' +
      "(the board); Kalshi settles on CF Benchmarks' index. Untested: 30 seconds, order-book imbalance, cross-exchange " +
      "confirmation.</p></div>";
  }

  function hourlyTable(hr, proven) {
    if (!hr || !hr.markets || !hr.markets.length) return '<p class="sub">No hourly above/below ladder open.</p>';
    var rows = hr.markets.map(function (r) {
      return "<tr><td>" + h(px(r.rule && r.rule.strike)) + '</td><td style="text-align:right">' + pct(r.p_model) +
        '</td><td style="text-align:right">' + num(r.yes_bid, 0) + "/" + num(r.yes_ask, 0) +
        '</td><td style="text-align:right">' + pct(r.p_cross) + '</td><td style="text-align:right">' +
        edgeCell(r.edge, "yes", proven) + '</td><td style="text-align:right">' + edgeCell(r.edge, "no", proven) + "</td></tr>";
    }).join("");
    var first = hr.markets[0] && hr.markets[0].rule;
    return '<div class="card" style="margin:8px 0;padding:10px 12px">' +
      '<div class="row-between"><b>Hourly: above the strike?</b><span class="sub">' + h(hr.event) + " · closes " +
      h(mt(hr.close_ts)) + "</span></div>" +
      '<p class="sub" style="margin:4px 0">Settles on: ' + (first ? h(first.says.replace(/ (above|below) [^ ]+$/, " $1 the strike")) : "–") +
      ". The nine strikes nearest the level" + (hr.unpriced ? "; " + h(hr.unpriced) + " market(s) whose rule did not parse are left out" : "") +
      ".</p>" + '<div style="overflow-x:auto"><table style="width:100%;font-size:0.85rem;font-variant-numeric:tabular-nums">' +
      '<tr style="color:var(--muted)"><th style="text-align:left">strike</th><th style="text-align:right">model</th>' +
      '<th style="text-align:right">bid/ask</th><th style="text-align:right" title="chance the level touches the strike before the close">touch</th>' +
      '<th style="text-align:right">YES edge</th><th style="text-align:right">NO edge</th></tr>' +
      rows + "</table></div>" + (proven ? "" : '<p class="sub" style="margin:4px 0">Edges are unproven: the model has not ' +
      "beaten Kalshi's price on the record.</p>") + "</div>";
  }

  function proxyLine(d) {
    var p = d.proxy || {};
    var src = (p.sources || []).map(function (s) {
      return '<span class="chip" title="' + h(s.ok ? (s.age_s != null ? s.age_s + " s old" : "no timestamp") +
        (s.stale ? " - stale, left out" : "") : (s.error || "unavailable")) + '">' + h(s.source) + " " +
        (s.ok ? h(px(s.mid)) + (s.dev_bps != null ? " (" + (s.dev_bps > 0 ? "+" : "") + h(num(s.dev_bps, 1)) + " bp)" : "") : "✗") +
        "</span>";
    }).join(" ");
    return '<div class="stats" style="margin:4px 0">Level <b>' + h(px(p.price)) + "</b> (" + h(p.basis || "") + ") · " +
      "realised vol <b>" + num(d.sigma_ann_pct, 0) + "%</b> a year</div><div>" + src + "</div>";
  }

  // ---- Kalshi's own price for the open 15-minute market, and the model's fair price this session ----
  function drawContract() {
    if (!st.charts) return;
    var C = st.charts, k = st.contract || {}, m = (st.board || {}).m15 || {};
    var cs = (k.candles || []).filter(function (x) { return x[0]; });
    var pt = function (i) { return cs.filter(function (x) { return x[i] != null; }).map(function (x) { return {time: x[0], value: x[i]}; }); };
    C.kya.setData(pt(2)); C.kyb.setData(pt(1));
    C.kna.setData(cs.filter(function (x) { return x[1] != null; }).map(function (x) { return {time: x[0], value: 100 - x[1]}; }));
    C.knb.setData(cs.filter(function (x) { return x[2] != null; }).map(function (x) { return {time: x[0], value: 100 - x[2]}; }));
    C.kfair.setData(st.fair.ticker === m.ticker ? st.fair.pts.slice() : []);
    var mk = [];
    ((st.paper || {}).open || []).concat(((st.paper || {}).recent || [])).forEach(function (p) {
      if (p.ticker !== m.ticker) return;
      var col = p.side === "yes" ? css("--yes", "#34d399") : css("--no", "#f87171");
      mk.push({time: Math.floor(p.entry_ts / 60) * 60, position: "belowBar", color: col, shape: "arrowUp",
               text: "paper buy " + p.side.toUpperCase() + " " + num(p.entry_c, 0) + "c"});
      if (p.exit_ts && p.exit_c != null) {
        mk.push({time: Math.floor(p.exit_ts / 60) * 60, position: "aboveBar", color: col, shape: "arrowDown",
                 text: "paper sell " + num(p.exit_c, 0) + "c"});
      }
    });
    var ks = {};
    cs.forEach(function (x) { if (x[2] != null) ks[x[0]] = 1; });
    C.kya.setMarkers(mk.filter(function (x) { return ks[x.time]; }).sort(function (a, b) { return a.time - b.time; }));
    var info = el("cxContractNote");
    if (info) {
      var fee = function (c) { return c == null ? null : 7 * c * (100 - c) / 10000; };
      info.textContent = m.ticker ? m.ticker + ": YES bid/ask " + num(m.yes_bid, 0) + "/" + num(m.yes_ask, 0) + "c (spread " +
        num(m.spread_c, 0) + "c, taker fee at the ask about " + num(fee(m.yes_ask), 2) + "c), NO bid/ask " + num(m.no_bid, 0) + "/" +
        num(m.no_ask, 0) + "c, volume " + num(m.volume, 0) + ", " + left(m.tau_s) + " left. Solid: asks; dotted: bids; green YES, " +
        "red NO; blue: the model's fair YES at each refresh this session (not stored)." : "";
    }
  }

  function paperHtml() {
    var P = st.paper, m = (st.board || {}).m15;
    var head = '<div class="card" style="margin:8px 0;padding:10px 12px"><b>Paper positions</b> <span class="chip">paper only</span>' +
      '<p class="sub" style="margin:4px 0">Simulated at Kalshi\'s executable prices when the server reads the market: buy at ' +
      "the ask, sell at the bid, the taker fee on each order rounded up to the cent; held to the close it settles at " +
      "Kalshi's result with no fee. No order is sent anywhere.</p>" +
      (m && m.ticker ? '<div class="row-between" style="gap:6px"><span class="sub">' + h(m.ticker) + '</span><span>' +
        '<input id="cxQty" type="number" min="1" max="100" value="1" style="width:64px" aria-label="contracts"> ' +
        '<button type="button" class="subtab" data-cx-open="yes">Paper-buy YES ' + num(m.yes_ask, 0) + 'c</button> ' +
        '<button type="button" class="subtab" data-cx-open="no">Paper-buy NO ' + num(m.no_ask, 0) + "c</button></span></div>" : "") +
      '<p class="sub" id="cxPaperMsg" style="margin:4px 0"></p>';
    if (!P) return head + "</div>";
    if (P.error) return head + '<p class="sub">' + h(P.error) + "</p></div>";
    var open = (P.open || []).map(function (p) {
      var e = p.eval || {}, o = e.other_side || {};
      return '<div style="border-top:1px solid var(--border);padding:6px 0"><div class="row-between"><span><b>' +
        h(p.side.toUpperCase()) + "</b> x" + h(p.qty) + " " + h(p.ticker) + " @ " + num(p.entry_c, 0) + "c (+" +
        num(p.entry_fee_c, 0) + "c fee)</span>" + '<button type="button" class="subtab" data-cx-close="' + h(p.id) +
        '">Paper-sell at the bid</button></div>' +
        '<div class="stats">Exit now (bid) <b>' + num(e.bid_c, 0) + "c</b> · if sold: " + num(e.close_value_c, 0) +
        "c after the fee, unrealized <b>" + (e.unrealized_c > 0 ? "+" : "") + num(e.unrealized_c, 0) + "c</b> · held " +
        h(left(p.held_s)) + " · " + h(left(p.tau_s)) + " left · the side's chance now " + pct(e.p_side) +
        " (at entry " + pct(p.p_entry) + ")</div>" +
        '<div class="stats">By the model: hold worth ' + num(e.hold_value_model_c, 0) + "c, sell " + num(e.close_value_c, 0) +
        "c → <b>" + h(e.verdict_by_model || "–") + "</b>. By Kalshi's own mid: hold worth " + num(e.hold_value_kalshi_mid_c, 0) +
        "c. Buying " + h(String(o.side || "").toUpperCase()) + " instead is its own decision: " + (o.edge_c == null ? "no ask" :
        (o.edge_c > 0 ? "+" : "") + num(o.edge_c, 1) + "c fee-in edge at " + num(o.ask_c, 0) + "c, " +
        (o.worth_it_by_model ? "clears" : "does not clear") + " the 3c rule") + ".</div>" +
        '<p class="sub" style="margin:2px 0">' + h(e.note || "") + "</p></div>";
    }).join("");
    var r = P.record || {};
    return head + (open || '<p class="sub">No open paper positions.</p>') +
      '<div class="stats" style="margin-top:6px">Closed and settled: ' + h(r.positions) + " positions, " +
      (r.pnl_c > 0 ? "+" : "") + num(r.pnl_c, 0) + "c after " + num(r.fees_c, 0) + "c of fees" +
      (r.positions ? " (" + num(r.per_position_c, 1) + "c a position ± " + num(r.se_c, 1) + ", win rate " + pct(r.win_rate) + ")" : "") +
      "</div></div>";
  }

  function recordHtml(x) {
    var r = x.record || {};
    if (!r.n) {
      return '<div class="card" style="margin:8px 0;padding:10px 12px"><b>Paper record</b><p class="sub" style="margin:4px 0">' +
        "Nothing graded yet. Each market is logged at fixed looks before its close (15-minute: 10, 5, 2 and 1 minute; " +
        "hourly: 30, 15 and 5) with Kalshi's book beside it, and graded off Kalshi's own settlement.</p></div>";
    }
    var small = (r.priced || 0) < ((x.decide || {}).markets || 500) || (r.days || 0) < ((x.decide || {}).days || 10);
    var call = r.decided === "model" ? "the model beat Kalshi's price past 2 SE" :
      r.decided === "kalshi" ? "Kalshi's price beat the model past 2 SE" : "not decided";
    var p = r.paper || {}, ex = x.extra || {}, iw = ex.intrawindow || {};
    var by = (x.by || []).map(function (b) {
      return "<tr><td>" + h(b.coin) + "</td><td>" + h(b.kind === "m15" ? "15 min" : "hourly") + '</td><td style="text-align:right">' +
        h(b.look_s) + ' s</td><td style="text-align:right">' + h(b.priced) + '</td><td style="text-align:right">' +
        num(b.model_logloss, 3) + '</td><td style="text-align:right">' + num(b.kalshi_logloss, 3) + "</td></tr>";
    }).join("");
    var reg = (ex.by_regime || []).map(function (b) {
      return h(b.regime) + ": " + num(b.model_logloss, 3) + " vs " + num(b.kalshi_logloss, 3) + " (" + h(b.n) + ")";
    }).join(" · ");
    return '<div class="card" style="margin:8px 0;padding:10px 12px"><b>Paper record: settlement</b> <span class="sub">at the decision ' +
      "look (15-minute 5 min, hourly 15 min before the close)</span>" +
      (small ? ' <span class="chip">small sample</span>' : "") +
      '<div class="stats" style="margin:4px 0">' + h(r.priced) + " rows priced both ways over " + h(r.days) + " day(s) · " +
      "log loss model <b>" + num(r.same_rows_model_logloss, 4) + "</b> vs Kalshi <b>" + num(r.same_rows_kalshi_logloss, 4) +
      "</b> (difference " + num(r.diff_logloss, 4) + " ± " + num(r.diff_se, 4) + ") · <b>" + h(call) + "</b></div>" +
      '<div class="stats">Paper rule (' + h(p.rule || "") + "): " + h(p.bets || 0) + " bets, " +
      (p.per_bet_c == null ? "–" : (p.per_bet_c > 0 ? "+" : "") + num(p.per_bet_c, 2) + "c a bet ± " + num(p.se_per_bet_c, 2)) +
      ", worst drawdown " + num(p.max_drawdown_c, 0) + "c.</div>" +
      (reg ? '<div class="stats">By volatility, model vs Kalshi log loss: ' + reg + "</div>" : "") +
      (r.proxy_rms_bps != null ? '<div class="stats">Proxy level a minute or more before the close vs the official average: ' +
        num(r.proxy_rms_bps, 1) + " bp RMS over " + h(r.proxy_n) + " markets (mostly the walk's own last-minute move, not " +
        "proxy error: the same minute measured " + "1.9-3.7 bp in research)</div>" : "") +
      "</div>" +
      '<div class="card" style="margin:8px 0;padding:10px 12px"><b>Paper record: trading inside the window</b>' +
      '<div class="stats" style="margin:4px 0">' + (iw.trades ? h(iw.rule) + ": " + h(iw.trades) + " trades, " +
        (iw.per_trade_c > 0 ? "+" : "") + num(iw.per_trade_c, 2) + "c a trade ± " + num(iw.se_c, 2) + ", total " +
        num(iw.total_c, 0) + "c, worst drawdown " + num(iw.max_drawdown_c, 0) + "c, turnover " + num(iw.turnover_c, 0) + "c" :
        "no completed trades yet") + "</div></div>" +
      (by ? '<details style="margin-top:6px"><summary class="sub">By coin, contract and look</summary>' +
        '<div style="overflow-x:auto"><table style="width:100%;font-size:0.82rem"><tr style="color:var(--muted)"><th>coin</th>' +
        '<th>contract</th><th style="text-align:right">look</th><th style="text-align:right">rows</th>' +
        '<th style="text-align:right">model LL</th><th style="text-align:right">Kalshi LL</th></tr>' + by + "</table></div></details>" : "");
  }

  function render(d) {
    st.board = d;
    if (d.off) { el("cxBoard").innerHTML = '<p class="sub">' + h(d.note) + "</p>"; return; }
    var proven = !!d.edges_proven;
    var m = d.m15;
    if (m && m.ticker && m.p_model != null) {                 // the model's fair YES this session, capped
      if (st.fair.ticker !== m.ticker) st.fair = {ticker: m.ticker, pts: []};
      var t = Math.floor(d.ts / 60) * 60, pts = st.fair.pts;
      if (pts.length && pts[pts.length - 1].time === t) pts[pts.length - 1].value = 100 * m.p_model;
      else pts.push({time: t, value: 100 * m.p_model});
      if (pts.length > 120) pts.splice(0, pts.length - 120);
    }
    el("cxHonest").innerHTML = honesty();
    el("cxBoard").innerHTML = proxyLine(d) + m15Card(m, proven) + whatIf(m) + hourlyTable(d.hourly, proven);
    el("cxPaper").innerHTML = paperHtml();
  }

  function bar() {
    var b = el("cxChartBar");
    if (!b) return;
    var tog = [["ema", "EMA 5/9/21"], ["vwap", "VWAP"], ["bb", "Bollinger"], ["sr", "60-bar range"], ["band", "forecast band"]];
    b.innerHTML = "<span>" + GRANS.map(function (g) {
      return '<button type="button" class="subtab" data-cx-gran="' + g[0] + '"' +
        (g[0] === st.gran ? ' aria-pressed="true" style="font-weight:600;border-color:var(--accent)"' : "") + ">" + h(g[1]) + "</button>";
    }).join(" ") + "</span><span>" + tog.map(function (t) {
      return '<label class="sub" style="margin-left:6px"><input type="checkbox" data-cx-show="' + t[0] + '"' +
        (st.show[t[0]] ? " checked" : "") + "> " + h(t[1]) + "</label>";
    }).join("") + "</span>";
  }

  function load() {
    if (st.busy) return;
    st.busy = true;
    st.polls += 1;
    var coin = st.coin || "BTC";
    var want = [getJSON("/api/crypto/board?coin=" + encodeURIComponent(coin)),
                LIVE_ONLY[st.gran] ? Promise.resolve(null) :
                  getJSON("/api/crypto/candles?coin=" + encodeURIComponent(coin) + "&gran=" + st.gran),
                st.rec && st.polls % 4 ? Promise.resolve(st.rec) : getJSON("/api/crypto/record"),
                st.research ? Promise.resolve(st.research) : getJSON("/api/crypto/research"),
                getJSON("/api/crypto/paper")];
    Promise.all(want).then(function (res) {
      var d = res[0] || {}, c = res[1], rec = res[2] || {};
      if (d.error) throw new Error(d.error);
      st.rec = rec.error ? st.rec : rec;
      st.research = res[3] || st.research;
      st.paper = res[4];
      if (!st.coin) {
        st.coin = d.coin;
        el("cxCoin").innerHTML = (d.coins || []).map(function (k) {
          return '<option value="' + h(k) + '"' + (k === d.coin ? " selected" : "") + ">" + h(k) + "</option>";
        }).join("");
      }
      if (c && c.candles) st.hist = c.candles.slice(-MAXBARS);
      render(d);
      if (d.symbol && !d.off) openWS(d.symbol);
      el("cxRecord").innerHTML = rec.error ? '<p class="sub">Record unavailable (' + h(rec.error) + ").</p>" : recordHtml(st.rec || {});
      var m = d.m15;
      return m && m.ticker ? getJSON("/api/crypto/contract?coin=" + encodeURIComponent(coin) + "&ticker=" +
                                     encodeURIComponent(m.ticker)) : null;
    }).then(function (k) {
      st.contract = k && !k.error ? k : null;
      drawPrice(); drawContract(); momentum();
    }).catch(function (err) {
      el("cxBoard").innerHTML = '<p class="sub">Could not load the crypto board (' + h(err && err.message) + ").</p>";
    }).then(function () {
      st.busy = false;
      arm();
    });
  }

  function paperMsg(t) { var x = el("cxPaperMsg"); if (x) x.textContent = t; }

  function wire() {
    if (st.wired) return;
    st.wired = true;
    bar();
    el("cxCoin").addEventListener("change", function (e) {
      st.coin = e.target.value; st.bars = {}; st.hist = []; st.lastPx = null; st.lastTick = 0; closeWS(); load();
    });
    el("cxChartBar").addEventListener("click", function (e) {
      var b = e.target.closest("[data-cx-gran]");
      if (!b) return;
      st.gran = Number(b.getAttribute("data-cx-gran"));
      st.hist = [];
      bar();
      load();
    });
    el("cxChartBar").addEventListener("change", function (e) {
      var k = e.target.getAttribute("data-cx-show");
      if (!k) return;
      st.show[k] = !!e.target.checked;
      drawPrice();
    });
    el("cxPaper").addEventListener("click", function (e) {
      var o = e.target.closest("[data-cx-open]"), c = e.target.closest("[data-cx-close]");
      var m = (st.board || {}).m15;
      if (o && m && m.ticker) {
        var q = Math.max(1, Math.min(100, Number((el("cxQty") || {}).value) || 1));
        paperMsg("Reading Kalshi's ask…");
        postJSON("/api/crypto/paper/open", {ticker: m.ticker, coin: st.coin, side: o.getAttribute("data-cx-open"), qty: q})
          .then(function (r) { paperMsg(r.error ? r.error : "Paper-bought at " + num(r.entry_c, 0) + "c."); load(); })
          .catch(function (err) { paperMsg(String(err && err.message)); });
      } else if (c) {
        paperMsg("Reading Kalshi's bid…");
        postJSON("/api/crypto/paper/close", {id: Number(c.getAttribute("data-cx-close"))})
          .then(function (r) { paperMsg(r.error ? r.error : "Paper-sold at " + num(r.exit_c, 0) + "c."); load(); })
          .catch(function (err) { paperMsg(String(err && err.message)); });
      }
    });
    document.addEventListener("visibilitychange", function () {
      if (onScreen() && !st.timer) load();
      else if (!onScreen()) closeWS();
    });
  }

  window.initCrypto = function () {
    wire();
    loadLib(function () { buildCharts(); if (!st.timer) load(); });
  };
})();
