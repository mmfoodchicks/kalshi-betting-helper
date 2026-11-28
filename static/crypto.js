// The crypto terminal (v1 2026-11-27, v2 the same day, v3 2026-11-28; crypto_terminal.py). Kalshi's 15-minute,
// hourly and daily crypto contracts priced off the rule each settles on. v3 is market-first: one tab per market,
// the chart spanning that market's OWN window (open to scheduled close, both read from Kalshi's metadata), the
// numbers that matter above it, plain English below it, research folded away. Display and paper only: this
// script reads /api/crypto/*, writes only to /api/crypto/paper/* (a row in Vigil's own file), and opens one
// public market-data WebSocket (Coinbase's ticker feed) in THIS browser while the tab is on screen -- the server
// holds no stream. Every server string is escaped (h); no inline handlers; one refresh timer, re-armed only while
// the tab is on screen and the page visible. Times show in Mountain time, the owner's clock. The visible window
// never limits a model: the server's models read their own trailing history, and the Advanced indicators are
// computed over the trailing candles before they are cut to the window.
(function () {
  "use strict";
  var ADV_GRANS = [[15, "15s"], [30, "30s"], [60, "1m"]];
  var LIVE_GRANS = [15, 30, 60, 300, 900];
  var LIVE_ONLY = {15: true, 30: true};          // Coinbase's REST candles start at one minute
  var REFRESH_MS = 15000;
  var MAXBARS = 480;                             // every bar buffer is capped (two hours of 15 s bars)
  var MAXPTS = 600;                              // a window's grid: 15 s over two hours, 5 min over a day
  var PAD = 0.05;                                // empty room either side of the window (a fraction of it, at least 4 slots), so the open and close markers are never clipped
  var LIB = "/static/vendor/lightweight-charts-4.1.3.standalone.production.js";
  var WS_URL = "wss://ws-feed.exchange.coinbase.com";
  var TABS = [["m15", "15 Minute"], ["hourly", "Hourly"], ["daily", "Daily"]];
  // stale thresholds: Coinbase's BTC feed ticks several times a second and DOGE's every few seconds (watched
  // 2026-11-27), so 30 s of silence is a dropped socket; the board refreshes every 15 s, so 60 s is two misses
  var STALE_FEED_S = 30, STALE_BOARD_S = 60;
  var st = {coin: null, tab: "m15", gran: 60, style: "line", prev: false, timer: null, busy: false, wired: false,
            board: null, boardAt: 0, loadErr: null, research: null, rec: null, bars: {}, hist: [], histKey: "",
            trail: [], ws: null, wsSym: null, lastTick: 0, lastPx: null, polls: 0, fair: {ticker: null, pts: []},
            paper: null, show: {ema: false, vwap: false, bb: false, sr: false}, band: 0, raf: false, lib: false,
            charts: null, contract: null, mode: "beginner", gridKey: "", winKey: "", moved: "", fitAt: 0,
            reloadAt: 0, textAt: 0, scaleK: null, minSpan: 0};
  var TECH = ["cxChartBar", "cxMomentum", "cxBoard", "cxRecord"];   // the Advanced view's containers
  // the forecast band's two-sided coverage -> its normal quantile; OFF by default (the owner: the 95% band was too
  // wide to read and fed the scaling problem), never part of the autoscale either way
  var BAND_Z = {50: 0.6745, 80: 1.2816, 95: 1.96};

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
  function dist(x) {
    if (x == null || !isFinite(x)) return "–";
    var lv = Math.abs(((st.board || {}).proxy || {}).price || x);
    return "$" + (lv >= 100 ? Number(Math.abs(x)).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2})
      : Number(Math.abs(x)).toPrecision(3));
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
  function usd(c) { return c == null || !isFinite(c) ? "–" : (c < 0 ? "-$" : "$") + Math.abs(c / 100).toFixed(2); }
  function css(v, dflt) {
    var x = getComputedStyle(document.documentElement).getPropertyValue(v);
    return (x && x.trim()) || dflt;
  }
  // a panel re-rendered every refresh keeps what the owner did to it: the folds he opened and the amount he typed
  function setHTML(id, html) {
    var box = el(id), open = {}, vals = {};
    if (!box) return;
    box.querySelectorAll("details[data-k]").forEach(function (x) { open[x.getAttribute("data-k")] = x.open; });
    box.querySelectorAll("input[id]").forEach(function (x) { vals[x.id] = x.value; });
    box.innerHTML = html;
    box.querySelectorAll("details[data-k]").forEach(function (x) { var k = x.getAttribute("data-k"); if (k in open) x.open = open[k]; });
    box.querySelectorAll("input[id]").forEach(function (x) { if (x.id in vals && x.type === "number") x.value = vals[x.id]; });
  }
  function chip(t, good, wrap) {          // wrap: a long label inside a narrow table cell may break at phone width
    return ' <span class="chip" style="border-color:' + (good ? "var(--yes)" : "var(--hold)") + (wrap ? ";white-space:normal" : "") + '">' + h(t) + "</span>";
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
        LIVE_GRANS.forEach(function (g) { addTick(g, t, p, v); });
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
    requestAnimationFrame(function () {
      st.raf = false;
      var w = win(), now = Date.now() / 1000;
      // the market closed: move to the next one at once (the 15 s poll would get there too)
      if (w && now > w.close_ts + 2 && now - st.reloadAt > 10 && !st.busy) { st.reloadAt = now; load(); return; }
      drawPrice();
      if (Date.now() - st.textAt > 1000) { st.textAt = Date.now(); statsHtml(); nowHtml(); momentum(); }
    });
  }

  // ---- pure helpers (no DOM; exercised by the guards through node) ----
  function pickStep(span, cands, maxN) {
    for (var i = 0; i < cands.length; i++) if (span / cands[i] <= maxN) return cands[i];
    return cands[cands.length - 1];
  }
  // the chart's grid for one window: [start, end) and the step of the line, the REST candles and the candles
  function plan(w, prev, mode, gran, style) {
    var start = prev && w.previous && w.previous.open_ts && w.previous.open_ts < w.open_ts ? w.previous.open_ts : w.open_ts;
    var end = w.close_ts, span = end - start;
    var rest = pickStep(span, [60, 300, 900, 3600], 300);                 // one REST call, <= 300 bars
    var line = pickStep(span, [15, 30, 60, 300, 900], MAXPTS);
    var cand = Math.max(mode === "beginner" ? rest : gran, line);
    var step = style === "line" ? line : cand;
    return {start: start, end: end, rest: rest, line: line, cand: cand, step: step,
            first: Math.floor(start / step) * step, last: Math.floor((end - 1) / step) * step,
            pad: Math.max(4, Math.ceil(PAD * span / step))};
  }
  function gridTimes(p) {
    var out = [];
    for (var t = p.first - p.pad * p.step; t <= p.last + p.pad * p.step && out.length < MAXPTS + 2 * p.pad; t += p.step) out.push(t);
    return out;
  }
  // the line: Coinbase's live bars where this browser has them, the REST candle's close (placed at the last slot
  // of its own period, where that close happened) wherever the live feed has nothing
  function lineData(hist, live, restG, step, start, end) {
    var have = {}, out = [];
    live.forEach(function (b) { have[Math.floor(b[0] / restG) * restG] = 1; });
    hist.forEach(function (b) {
      var t = b[0] + restG - step;
      if (!have[b[0]] && t >= start && t < end) out.push({time: t, value: b[4]});
    });
    live.forEach(function (b) { if (b[0] >= start && b[0] < end) out.push({time: b[0], value: b[4]}); });
    out.sort(function (a, b) { return a.time - b.time; });
    return out.filter(function (x, i) { return !i || x.time !== out[i - 1].time; });
  }
  // candles: the REST candles with the live feed refreshing the newest; 15 s / 30 s from the live feed alone
  function candleData(hist, live, restG, step, start, end) {
    var base;
    if (step === restG) {
      base = hist.map(function (b) { return b.slice(); });
      var last = base.length ? base[base.length - 1][0] : 0;
      live.forEach(function (b) {
        if (b[0] > last) base.push(b.slice());
        else if (b[0] === last) { var x = base[base.length - 1]; x[2] = Math.max(x[2], b[2]); x[3] = Math.min(x[3], b[3]); x[4] = b[4]; }
      });
    } else {
      base = live.slice();
    }
    return base.filter(function (b) { return b[0] >= start && b[0] < end; });
  }
  // the price axis: the observed movement, never narrower than minSpan (a quiet window would otherwise blow
  // a $40 drift up to fill 500 px), the target included only while that costs at most 2.5x the movement's
  // span (a far strike would crush the movement into a line: it gets an off-screen badge instead), 8% padding
  function scaleRange(lo, hi, k, minSpan) {
    var mid = (lo + hi) / 2, span = Math.max(hi - lo, minSpan || 0);
    lo = Math.min(lo, mid - span / 2); hi = Math.max(hi, mid + span / 2);
    var inK = k != null && isFinite(k) && k >= lo - 1.5 * span && k <= hi + 1.5 * span;
    if (inK) { lo = Math.min(lo, k); hi = Math.max(hi, k); }
    var pad = 0.08 * (hi - lo);
    return {lo: lo - pad, hi: hi + pad, targetIn: inK};
  }
  // what HAS happened in the window: direction over 3 minutes, measured against the walk's own 3-minute move
  // (|z| < 0.5 is "sideways"), and whether the last 90 s moved more or less than the 90 before
  function observed(pts, sig, now) {
    if (!pts.length || !sig) return null;
    var at = function (t) {
      var v = null;
      for (var i = 0; i < pts.length && pts[i].time <= t; i++) v = pts[i].value;
      return v;
    };
    var v0 = pts[pts.length - 1].value, t0 = Math.min(now, pts[pts.length - 1].time + 15);
    if (pts[0].time > t0 - 180) return {short: true};
    var v3 = at(t0 - 180), v90 = at(t0 - 90);
    var z = Math.log(v0 / v3) / (sig * Math.sqrt(180));
    var a = Math.log(v0 / v90) / (sig * Math.sqrt(90)), b = Math.log(v90 / v3) / (sig * Math.sqrt(90));
    var dir = z > 0.5 ? "rising" : z < -0.5 ? "falling" : "sideways", s = dir === "rising" ? 1 : -1, str = null;
    if (dir !== "sideways") str = a * s > Math.abs(b) + 0.25 ? "strengthening" : a * s < b * s - 0.25 ? "weakening" : "steady";
    return {dir: dir, z: z, move: v0 - v3, strength: str, recent: a, before: b};
  }
  // the paper position's cash-out value since entry, a compact SVG (numbers only: nothing from the server is text)
  function svgValue(v, W, H) {
    var pts = (v && v.points) || [];
    if (!pts.length) return "";
    var t0 = Math.min(v.entry_ts || pts[0][0], pts[0][0]), t1 = Math.max(pts[pts.length - 1][0], t0 + 60);
    var vals = pts.map(function (p) { return p[1]; }).concat([v.cost_c]);
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    if (hi - lo < 1) { hi += 1; lo -= 1; }
    var pad = 0.12 * (hi - lo);
    lo -= pad; hi += pad;
    var X = function (t) { return (4 + (W - 8) * (t - t0) / (t1 - t0)).toFixed(1); };
    var Y = function (c) { return (H - 4 - (H - 8) * (c - lo) / (hi - lo)).toFixed(1); };
    var line = pts.map(function (p) { return X(p[0]) + "," + Y(p[1]); }).join(" ");
    var last = pts[pts.length - 1][1], col = last >= v.cost_c ? "var(--yes)" : "var(--no)";
    return '<svg viewBox="0 0 ' + W + " " + H + '" width="100%" height="' + H + '" preserveAspectRatio="none" role="img" ' +
      'aria-label="cash-out value since entry">' +
      '<line x1="0" x2="' + W + '" y1="' + Y(v.cost_c) + '" y2="' + Y(v.cost_c) + '" stroke="currentColor" ' +
      'stroke-opacity="0.45" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>' +
      '<polyline fill="none" stroke="' + col + '" stroke-width="2" vector-effect="non-scaling-stroke" points="' + line + '"/>' +
      '<circle cx="' + X(pts[0][0]) + '" cy="' + Y(pts[0][1]) + '" r="3" fill="var(--accent)"/></svg>';
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
  function chartH() { return window.innerWidth < 640 ? 340 : 500; }
  function boxW() { return Math.max(280, (el("cxChartWrap") || {}).clientWidth || 360); }
  function mkChart(box, height) {
    var LC = window.LightweightCharts;
    return LC.createChart(box, {
      width: boxW(), height: height,
      layout: {background: {type: "solid", color: "transparent"}, textColor: css("--muted", "#95a1b3"), fontSize: 11},
      grid: {vertLines: {color: "rgba(255,255,255,0.04)"}, horzLines: {color: "rgba(255,255,255,0.04)"}},
      crosshair: {mode: LC.CrosshairMode.Normal},
      rightPriceScale: {borderColor: "rgba(255,255,255,0.1)", alignLabels: true},
      timeScale: {borderColor: "rgba(255,255,255,0.1)", timeVisible: true, secondsVisible: false, rightOffset: 2,
                  shiftVisibleRangeOnNewBar: false, tickMarkFormatter: function (t) { return mtFmt(t); }},
      localization: {timeFormatter: function (t) { return mtFmt(t, true) + " MT"; }},
      handleScroll: true,
      handleScale: {axisPressedMouseMove: {time: true, price: true}, mouseWheel: true, pinch: true, axisDoubleClickReset: true}
    });
  }
  function buildCharts() {
    if (st.charts) return;
    var box = el("cxChart");
    box.style.height = chartH() + "px";
    var c = mkChart(box, chartH()), k = mkChart(el("cxContract"), 200);
    var none = function () { return null; };       // an overlay never stretches the price axis
    var line = function (ch, col, w, style, keep) {
      return ch.addLineSeries({color: col, lineWidth: w || 1, lineStyle: style || 0, priceLineVisible: false,
                               lastValueVisible: false, crosshairMarkerVisible: false,
                               autoscaleInfoProvider: keep ? undefined : none});
    };
    var yes = css("--yes", "#34d399"), no = css("--no", "#f87171"), acc = css("--accent", "#5b8cff"),
        hold = css("--hold", "#fbbf24"), faint = css("--faint", "#5d6678");
    var fit = function (orig) {                    // the main series: movement + target, see scaleRange
      var r = orig();
      if (!r || !r.priceRange) return r;
      var s = scaleRange(r.priceRange.minValue, r.priceRange.maxValue, st.scaleK, st.minSpan);
      return {priceRange: {minValue: s.lo, maxValue: s.hi}};
    };
    st.charts = {
      price: c, contract: k,
      grid: c.addLineSeries({color: "rgba(0,0,0,0)", lineVisible: false, lastValueVisible: false, priceLineVisible: false,
                             crosshairMarkerVisible: false, autoscaleInfoProvider: none}),
      line: c.addLineSeries({color: acc, lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
                             autoscaleInfoProvider: fit}),
      candles: c.addCandlestickSeries({upColor: yes, downColor: no, wickUpColor: yes, wickDownColor: no, borderVisible: false,
                                       priceLineVisible: false, autoscaleInfoProvider: fit}),
      prevLine: line(c, "rgba(149,161,179,0.75)", 2, 0, true),
      prevK: line(c, "rgba(251,191,36,0.55)", 1, 2),
      edge: line(c, "rgba(0,0,0,0)", 1, 0),
      vol: c.addHistogramSeries({priceFormat: {type: "volume"}, priceScaleId: "vol", color: "rgba(149,161,179,0.35)",
                                 lastValueVisible: false, priceLineVisible: false}),
      e5: line(c, "#c084fc"), e9: line(c, "#22d3ee"), e21: line(c, hold), vwap: line(c, "#e8edf4", 1, 1),
      bbu: line(c, faint, 1, 2), bbl: line(c, faint, 1, 2), bandu: line(c, acc, 1, 2), bandl: line(c, acc, 1, 2),
      med: line(c, "rgba(91,140,255,0.8)", 1, 1),
      lines: [], linesOn: null,
      kya: line(k, yes, 2, 0, true), kyb: line(k, yes, 1, 2, true), kna: line(k, no, 2, 0, true),
      knb: line(k, no, 1, 2, true), kfair: line(k, acc, 2, 0, true)
    };
    // volume sits in its own band under the price (its own scale), never on the price axis
    c.priceScale("vol").applyOptions({scaleMargins: {top: 0.84, bottom: 0}});
    window.addEventListener("resize", function () {
      if (!st.charts) return;
      el("cxChart").style.height = chartH() + "px";
      st.charts.price.applyOptions({width: boxW(), height: chartH()});
      st.charts.contract.applyOptions({width: boxW()});
      badge();
    });
  }

  function win() { var W = (st.board || {}).windows || {}; return W[st.tab] || null; }
  function liveLevel() {
    var now = Date.now() / 1000;
    return st.lastPx && now - st.lastTick < STALE_FEED_S ? st.lastPx : ((st.board || {}).proxy || {}).price;
  }

  function viewData(w) {
    var p = plan(w, st.prev, st.mode, st.gran, st.style);
    var key = st.coin + "|" + p.rest + "|" + p.start + "|" + p.end;
    var hist = st.histKey === key ? st.hist : [];
    var liveLine = st.bars[p.line] || [], liveCand = st.bars[p.cand] || [];
    return {p: p, pts: lineData(hist, liveLine, p.rest, p.line, p.start, p.end),
            bars: candleData(hist, liveCand, p.rest, p.cand, p.start, p.end), hist: hist};
  }

  function drawPrice() {
    if (!st.charts) return;
    var C = st.charts, d = st.board || {}, w = win(), note = el("cxChartNote");
    var clearAll = function () {
      ["grid", "line", "candles", "prevLine", "prevK", "edge", "vol", "e5", "e9", "e21", "vwap", "bbu", "bbl", "bandu", "bandl", "med"]
        .forEach(function (k) { C[k].setData([]); });
      C.lines.forEach(function (l) { C.linesOn.removePriceLine(l); });
      C.lines = [];
      st.gridKey = "";
    };
    if (!w) { clearAll(); badge(); if (note) note.textContent = ""; return; }
    var V = viewData(w), p = V.p, k = ((w.target || {}).rule || {}).strike, prev = w.previous || null;
    var gk = p.first + "|" + p.last + "|" + p.step;
    if (gk !== st.gridKey) {                       // a new window, the previous toggle, or a new step: refit
      C.grid.setData(gridTimes(p).map(function (t) { return {time: t}; }));
      st.gridKey = gk;
      st.refit = true;
    }
    var lv = liveLevel(), sg = d.sigma_s;
    st.scaleK = k;
    st.minSpan = lv && sg ? 4 * lv * sg * Math.sqrt(60) : 0;    // +-2 SDs of one minute's move
    var cur = V.pts.filter(function (x) { return x.time >= w.open_ts; }),
        old = V.pts.filter(function (x) { return x.time < w.open_ts; });
    var useLine = st.style === "line";
    C.line.setData(useLine ? cur : []);
    C.prevLine.setData(useLine ? old : []);
    var muted = "rgba(149,161,179,0.55)";
    C.candles.setData(useLine ? [] : V.bars.map(function (b) {
      var o = {time: b[0], open: b[1], high: b[2], low: b[3], close: b[4]};
      if (b[0] < w.open_ts) { o.color = muted; o.wickColor = muted; }   // the previous window, greyed
      return o;
    }));
    C.vol.setData(useLine ? [] : V.bars.map(function (b) {
      return {time: b[0], value: b[5], color: b[4] >= b[1] ? "rgba(52,211,153,0.3)" : "rgba(248,113,113,0.3)"};
    }));
    C.price.priceScale("right").applyOptions({scaleMargins: {top: 0.06, bottom: useLine ? 0.06 : 0.2}});
    // the previous window's own target, drawn over that window only
    var ps = old.length || (!useLine && V.bars.length && V.bars[0][0] < w.open_ts);
    C.prevK.setData(st.prev && prev && prev.strike && ps ? [{time: p.first, value: prev.strike},
      {time: Math.floor((w.open_ts - 1) / p.step) * p.step, value: prev.strike}] : []);
    C.prevK.setMarkers(st.prev && prev && prev.strike && ps ? [{time: p.first, position: "aboveBar", color: css("--hold", "#fbbf24"),
      shape: "circle", text: "previous target " + px(prev.strike)}] : []);
    // indicators (Advanced): computed over the trailing candles, then cut to the window
    var adv = st.mode !== "beginner", ind = adv && (st.show.ema || st.show.vwap || st.show.bb);
    var src = ind ? indSource(p) : [], s = src.length ? series(src) : null;
    var ln = function (arr, skip) {
      var out = [];
      src.forEach(function (b, i) { if (i >= (skip || 0) && b[0] >= p.start && b[0] < p.end) out.push({time: b[0], value: arr[i]}); });
      return out;
    };
    C.e5.setData(s && st.show.ema ? ln(s.e5) : []); C.e9.setData(s && st.show.ema ? ln(s.e9) : []);
    C.e21.setData(s && st.show.ema ? ln(s.e21) : []); C.vwap.setData(s && st.show.vwap ? ln(s.vwap) : []);
    C.bbu.setData(s && st.show.bb ? ln(s.up, 19) : []); C.bbl.setData(s && st.show.bb ? ln(s.lo, 19) : []);
    // price lines: the target always; the index (proxy) unless it sits on the live price (then its axis label
    // alone, so two titles never overprint); the window's range in Advanced
    var main = useLine ? C.line : C.candles;
    C.lines.forEach(function (l) { C.linesOn.removePriceLine(l); });
    C.lines = [];
    C.linesOn = main;
    var pl = function (y, col, title, style, lw) {
      if (y == null || !isFinite(y)) return;
      C.lines.push(main.createPriceLine({price: y, color: col, lineWidth: lw || 1, lineStyle: style, axisLabelVisible: true, title: title}));
    };
    pl(k, css("--hold", "#fbbf24"), "Target", 2, 2);
    var pr = (d.proxy || {}).price, yP = pr ? main.priceToCoordinate(pr) : null, yL = lv ? main.priceToCoordinate(lv) : null,
        yK = k ? main.priceToCoordinate(k) : null;
    var crowded = yP != null && ((yL != null && Math.abs(yP - yL) < 16) || (yK != null && Math.abs(yP - yK) < 16));
    pl(pr, "rgba(91,140,255,0.6)", crowded ? "" : "Index (proxy)", 1);
    if (adv && st.show.sr && V.bars.length) {
      var cb = V.bars.filter(function (b) { return b[0] >= w.open_ts; });
      if (cb.length) {
        pl(Math.max.apply(null, cb.map(function (b) { return b[2]; })), "rgba(232,237,244,0.35)", "window high", 3);
        pl(Math.min.apply(null, cb.map(function (b) { return b[3]; })), "rgba(232,237,244,0.35)", "window low", 3);
      }
    }
    // the FORECAST band (optional, off by default): the random walk's 50 / 80 / 95% range from the last
    // observation to this market's close, dashed and outside the autoscale, with its MEDIAN -- the price now, flat,
    // because the walk has no direction and the validated tilt is a 1-3 point probability, not a price path
    var band = [[], [], []], lastT = cur.length ? cur[cur.length - 1].time : (V.bars.length ? V.bars[V.bars.length - 1][0] : null);
    var Z = BAND_Z[st.band];
    if (Z && lastT != null && lv && sg) {
      for (var t = lastT; t <= p.last; t += p.step) {
        var sd = sg * Math.sqrt(Math.max(0, t - lastT) + 1e-9);
        band[0].push({time: t, value: lv * Math.exp(Z * sd)}); band[1].push({time: t, value: lv * Math.exp(-Z * sd)});
        band[2].push({time: t, value: lv});
      }
    }
    C.bandu.setData(band[0]); C.bandl.setData(band[1]); C.med.setData(band[2]);
    // the scheduled close, from Kalshi's close_time, marked on the grid's last slot
    C.edge.setData(lv ? [{time: p.last, value: lv}] : []);
    C.edge.setMarkers(lv ? [{time: p.last, position: "aboveBar", color: css("--muted", "#95a1b3"), shape: "arrowDown",
                             text: window.innerWidth < 640 ? "close" : "closes " + mtFmt(w.close_ts)}] : []);
    main.setMarkers(markers(main === C.line ? cur.map(function (x) { return x.time; }) : V.bars.map(function (b) { return b[0]; }),
                            w, p, adv, s, src));
    (main === C.line ? C.candles : C.line).setMarkers([]);
    if (st.refit) { st.refit = false; fit(st.prev); }       // with the previous window on, show both windows
    badge();
    if (note) note.innerHTML = chartNote(w, p, V);
  }

  function indSource(p) {
    if (p.cand === 60 && st.trail.length) return candleData(st.trail, st.bars[60] || [], 60, 60, -Infinity, Infinity);
    var V = viewData(win());
    return V.bars;
  }

  function markers(times, w, p, adv, s, src) {
    if (!times.length) return [];
    var out = [], snap = function (ts) {            // the nearest drawn point at or before ts
      var best = null;
      for (var i = 0; i < times.length && times[i] <= ts; i++) best = times[i];
      return best;
    };
    var tOpen = snap(Math.max(w.open_ts, times[0]));
    if (tOpen != null && times[0] <= w.open_ts + Math.max(p.rest, p.step)) {
      out.push({time: tOpen, position: "belowBar", color: css("--muted", "#95a1b3"), shape: "arrowUp", text: window.innerWidth < 640 ? "open" : "opened " + mtFmt(w.open_ts)});
    }
    // paper entries and exits on this market: OBSERVED events at the times they happened, never a forecast
    var P = st.paper || {};
    (P.open || []).concat(P.recent || []).forEach(function (x) {
      if (x.ticker !== w.ticker) return;
      var col = x.side === "yes" ? css("--yes", "#34d399") : css("--no", "#f87171"), te = snap(x.entry_ts);
      if (te != null) out.push({time: te, position: "belowBar", color: col, shape: "arrowUp", text: "paper buy " + x.side.toUpperCase()});
      if (x.exit_ts && snap(x.exit_ts) != null) {
        out.push({time: snap(x.exit_ts), position: "aboveBar", color: col, shape: "arrowDown",
                  text: x.status === "settled" ? "settled" : "paper sell"});
      }
    });
    if (adv) {
      var sig = ((((st.rec || {}).extra || {}).signals || {})[st.coin]) || [];
      sig.forEach(function (x) {
        if (!x.side || x.ts < p.start || snap(x.ts) == null) return;
        var hit = x.result == null || x.result < 0 ? "" : ((x.side === "yes") === (x.result === 1) ? " ✓" : " ✗");
        out.push({time: snap(x.ts), position: x.side === "yes" ? "belowBar" : "aboveBar",
                  color: x.side === "yes" ? css("--yes", "#34d399") : css("--no", "#f87171"),
                  shape: x.side === "yes" ? "arrowUp" : "arrowDown", text: "signal " + x.side.toUpperCase() + hit});
      });
      if (s && st.show.ema) {
        for (var i = 22; i < src.length; i++) {
          var a = s.e9[i - 1] - s.e21[i - 1], b = s.e9[i] - s.e21[i];
          if (a * b < 0 && src[i][0] >= p.start && snap(src[i][0]) != null) {
            out.push({time: snap(src[i][0]), position: "inBar", color: css("--hold", "#fbbf24"), shape: "circle",
                      text: b > 0 ? "EMA up-cross" : "EMA down-cross"});
          }
        }
      }
      // past outputs of the 5-minute model at 60%+ (each logged when it happened; never a future time)
      (((st.board || {}).direction || {}).marks || []).forEach(function (x) {
        if (x[0] < p.start || snap(x[0]) == null) return;
        out.push({time: snap(x[0]), position: x[2] > 0 ? "aboveBar" : "belowBar", color: css("--accent", "#5b8cff"),
                  shape: "square", text: "possible reversal " + Math.round(100 * x[1]) + "%"});
      });
    }
    var seen = {};
    return out.sort(function (x, y) { return x.time - y.time; }).filter(function (x) {
      var k = x.time + x.text;
      if (seen[k]) return false;
      seen[k] = 1;
      return true;
    });
  }

  // the strike off the chart: say where and how far, rather than crush the movement to fit it
  function badge() {
    var b = el("cxOff"), C = st.charts, w = win();
    if (!b || !C) return;
    var k = w && ((w.target || {}).rule || {}).strike, main = st.style === "line" ? C.line : C.candles;
    var y = k ? main.priceToCoordinate(k) : null, H = chartH() - 28, lv = liveLevel();
    if (y == null || (y >= 0 && y <= H) || !lv) { b.classList.add("hidden"); return; }
    var above = y < 0;
    b.style.order = above ? "-1" : "1";
    b.textContent = (above ? "▲ " : "▼ ") + "Target " + px(k) + " is " + dist(k - lv) + (above ? " above" : " below") +
      " the price (off the chart)";
    b.classList.remove("hidden");
  }

  function fit(all) {
    var C = st.charts, w = win();
    if (!C || !w) return;
    var p = plan(w, st.prev, st.mode, st.gran, st.style);
    st.fitAt = Date.now();
    try {
      C.price.timeScale().setVisibleRange({from: (all ? p.first : Math.floor(w.open_ts / p.step) * p.step) - p.pad * p.step,
                                           to: p.last + p.pad * p.step});
    } catch (e) {
      C.price.timeScale().fitContent();
    }
    C.price.priceScale("right").applyOptions({autoScale: true});
  }

  function chartNote(w, p, V) {
    var prev = w.previous, live = st.lastTick && Date.now() / 1000 - st.lastTick < STALE_FEED_S;
    var parts = [(st.style === "line" ? "Line: " : "Candles (" + (p.cand < 60 ? p.cand + " s" : p.cand / 60 + " min") + "): ") +
      (st.style !== "line" && p.cand < p.rest ? "built from Coinbase's trades in this browser since the tab opened (Coinbase " +
        "keeps no sub-minute history, so the window before that is empty)." :
       live ? "Coinbase's trades, live in this browser, filled before the tab opened from Coinbase's " +
        (p.rest / 60) + "-minute candles." : "Coinbase's " + (p.rest / 60) + "-minute candles (the live feed is not connected).") +
      " The window runs from Kalshi's open (" + mt(w.open_ts) + ") to its scheduled close (" + mt(w.close_ts) + ")."];
    parts.push("Dashed gold: the target. Blue: the index level the board reads (a proxy for CF Benchmarks' licensed index).");
    if (BAND_Z[st.band]) {
      parts.push("Dashed blue band: a FORECAST, the random walk's " + st.band + "% range to the close; the dotted line is its " +
        "median, the price now (the walk has no direction).");
    }
    if (st.prev) {
      parts.push(prev && prev.strike ? "Grey: the previous window, with ITS OWN target " + px(prev.strike) +
        (prev.settle ? " (settled at " + px(prev.settle) + (prev.result ? ", " + prev.result.toUpperCase() : "") + ")" : "") +
        (prev.strike_basis ? "; " + prev.strike_basis : "") + "." : "The previous window's target is not available.");
    }
    if (w.target_basis) parts.push("This event lists many strikes; the target shown is " + w.target_basis + ".");
    if (st.moved) parts.push(st.moved);
    if (!V.pts.length && !V.bars.length) parts.push("Waiting for data.");
    return h(parts.join(" "));
  }

  // ---- above the chart: the numbers that matter ----
  function tile(label, value, sub, warn) {
    return '<div style="padding:6px 8px;border:1px solid ' + (warn ? "var(--hold)" : "var(--border)") + ';border-radius:8px">' +
      '<div class="sub" style="font-size:0.72rem">' + h(label) + '</div><div style="font-size:1.05rem;font-weight:600;' +
      'font-variant-numeric:tabular-nums">' + h(value) + '</div><div class="sub" style="font-size:0.7rem">' + h(sub) + "</div></div>";
  }
  function tabName(t) { for (var i = 0; i < TABS.length; i++) if (TABS[i][0] === t) return TABS[i][1]; return t; }
  function statsHtml() {
    var box = el("cxStats"), d = st.board;
    if (!box || !d || d.off) return;
    var w = win(), now = Date.now() / 1000;
    if (!w) {
      var dev = ((d.windows || {}).daily_event);
      box.innerHTML = '<div class="card" style="margin:6px 0;padding:10px 12px">' + (st.tab === "daily" ? (dev ?
        "Kalshi lists the daily event " + h(dev) + ", but its markets could not be read just now; trying again on the next refresh." :
        "No daily market (closing 5 PM Eastern) is open for " + h(st.coin) + " right now: Kalshi lists none closing in the next 26 hours.") :
        "No open " + h(tabName(st.tab)) + " market for " + h(st.coin) + " right now.") + "</div>";
      return;
    }
    var r = w.target || {}, k = (r.rule || {}).strike, live = st.lastPx && now - st.lastTick < STALE_FEED_S;
    var lv = liveLevel(), diff = lv != null && k != null ? lv - k : null;
    var fa = st.lastTick ? now - st.lastTick : null, ba = st.boardAt ? now - st.boardAt : null;
    var feedBad = fa == null || fa > STALE_FEED_S, boardBad = ba == null || ba > STALE_BOARD_S || !!st.loadErr;
    var fresh = (feedBad ? "live feed " + (fa == null ? "not connected" : "silent " + num(fa, 0) + " s") : "live " + num(fa, 0) + " s") +
      " · prices " + (ba == null ? "–" : num(ba, 0) + " s");
    box.innerHTML = '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:6px;margin:6px 0">' +
      tile("Price", px(lv), live ? "Coinbase, live" : "4-exchange median (proxy)", feedBad) +
      tile("Target", px(k), r.rule ? "Kalshi's strike" : "rule did not parse") +
      tile(diff == null ? "Distance" : diff >= 0 ? "Above target" : "Below target", diff == null ? "–" : dist(diff),
           diff == null ? "" : (diff >= 0 ? "YES side" : "NO side") + " right now") +
      tile("Time left", left(w.close_ts - now), "closes " + mt(w.close_ts)) +
      tile("YES buy price", r.yes_ask == null ? "none" : num(r.yes_ask, 0) + "¢", "pays $1 if YES") +
      tile("NO buy price", r.no_ask == null ? "none" : num(r.no_ask, 0) + "¢", "pays $1 if NO") +
      tile("Market says YES", r.kalshi_mid == null ? "–" : num(r.kalshi_mid, 0) + "%", r.kalshi_mid == null ? "one-sided book" : "Kalshi's mid") +
      tile("Vigil (experimental)", pct(r.p_model), "random walk, unproven") +
      tile("Data", feedBad || boardBad ? "stale" : "fresh", fresh + (st.loadErr ? " · server unreachable" : ""), feedBad || boardBad) +
      "</div>" + (boardBad && ba != null ? '<p class="sub" style="margin:2px 0;color:var(--hold)">The prices above are ' + h(num(ba, 0)) +
        " s old" + (st.loadErr ? " (the server did not answer: " + h(st.loadErr) + ")" : "") + ": do not act on them.</p>" : "");
  }

  // ---- below the chart: what HAS happened, apart from what the models PREDICT ----
  function nowHtml() {
    var box = el("cxNow"), d = st.board, w = win();
    if (!box || !d || d.off || !w) { if (box) box.innerHTML = ""; return; }
    var r = w.target || {}, now = Date.now() / 1000, sg = d.sigma_s;
    var V = viewData(w), cur = V.pts.filter(function (x) { return x.time >= w.open_ts; });
    var o = observed(cur.length >= 2 ? cur : V.pts, sg, now);
    var obs1, obs2;
    if (!o) { obs1 = "No prices for this window yet."; obs2 = ""; }
    else if (o.short) { obs1 = "Less than three minutes of this window observed so far."; obs2 = ""; }
    else {
      obs1 = "<b>" + h(o.dir[0].toUpperCase() + o.dir.slice(1)) + "</b>: " + (o.move >= 0 ? "up " : "down ") + h(dist(o.move)) +
        " over the last 3 minutes (" + num(Math.abs(o.z), 1) + "x a typical 3-minute move" + (o.dir === "sideways" ? ", inside the noise" : "") + ").";
      obs2 = o.strength ? "<b>" + h(o.strength[0].toUpperCase() + o.strength.slice(1)) + "</b>: the last 90 seconds moved " +
        num(o.recent, 1) + " SDs against " + num(o.before, 1) + " in the 90 before." : "No direction to strengthen or weaken.";
    }
    var col = function (title, sub, body) {
      return '<div style="flex:1 1 300px;min-width:0;padding:8px 12px;border:1px solid var(--border);border-radius:8px">' +
        "<b>" + h(title) + '</b> <span class="sub">' + h(sub) + "</span>" + body + "</div>";
    };
    box.innerHTML = '<div style="display:flex;flex-wrap:wrap;gap:8px;margin:8px 0">' +
      col("What has happened", "observed, this window", '<ul style="margin:4px 0 0 18px;padding:0">' +
        [obs1, obs2].filter(Boolean).map(function (x) { return "<li>" + x + "</li>"; }).join("") + "</ul>") +
      col("Next movement outlook", "forecasts, not facts", outlookHtml(w, r, now)) + "</div>" + scenariosHtml(w, now);
  }

  // one horizontal bar, Up green / Down red, in the owner's mockup's style; a figure with no validated lean is
  // drawn grey and says "Direction uncertain" -- never a coloured lean the evidence does not carry
  function splitBar(label, up, tag, good, note, grey) {
    var u = up == null ? 50 : 100 * up, body = grey ?
      '<div style="flex:1;background:rgba(149,161,179,0.22);text-align:center;line-height:20px">Direction uncertain</div>' :
      '<div style="width:' + u.toFixed(1) + '%;background:rgba(52,211,153,0.55);padding-left:6px;line-height:20px;white-space:nowrap">Up ' +
        u.toFixed(1) + '%</div><div style="width:' + (100 - u).toFixed(1) + '%;background:rgba(248,113,113,0.55);text-align:right;' +
        'padding-right:6px;line-height:20px;white-space:nowrap">Down ' + (100 - u).toFixed(1) + "%</div>";
    return '<div style="margin:8px 0 2px"><div class="row-between" style="font-size:0.85rem;gap:4px"><span>' + h(label) + "</span>" +
      chip(tag, good) + '</div><div style="display:flex;height:20px;border-radius:5px;overflow:hidden;font-size:0.74rem;' +
      'font-weight:600">' + body + '</div><div class="sub" style="font-size:0.76rem;margin-top:2px">' + note + "</div></div>";
  }
  function hzName(s) { return s < 60 ? "Next " + s + " s" : "Next " + s / 60 + " min"; }
  function outlookHtml(w, r, now) {
    var O = (st.board || {}).outlook || {}, left_s = w.close_ts - now, out = [], leans = 0;
    (O.direction || []).forEach(function (x) {
      if (x.h_s > left_s) return;                       // a 5-minute bar with two minutes left says nothing
      if (x.status === "validated") {
        leans += 1;
        var ho = (x.heldout || {}).at_or_above || {};
        out.push(splitBar(hzName(x.h_s), x.p_up, "validated", true, "A small " + (x.p_up >= 0.5 ? "upward" : "downward") +
          " lean. On held-out minutes, leans at least this size picked the side " + pct(ho.hit) + " of the time."));
      } else {
        out.push(splitBar(hzName(x.h_s), null, x.status === "untested" ? "untested" : x.status === "null" ? "random walk" :
          "lean within the noise", false, h(x.why || "") + (x.status === "uncertain" && x.p_up != null ? " (the model reads " +
          pct(x.p_up) + " up)." : "."), true));
      }
    });
    var rec = (st.rec || {}).record || {}, losing = rec.same_rows_model_logloss != null && rec.same_rows_model_logloss > rec.same_rows_kalshi_logloss;
    if (r.p_model != null) {
      out.push(splitBar("At settlement: above target", r.p_model, "random walk (null)", false, "Vigil's random walk, " +
        (losing ? "losing to Kalshi's price on the live record" : "unproven against Kalshi's price") + ". Kalshi's price says YES " +
        (r.kalshi_mid == null ? "–" : num(r.kalshi_mid, 0) + "%") + ". Settles on " + (r.rule ? h(r.rule.says) :
        "a rule that did not parse") + "." + (r.iv_note ? ' <span style="color:var(--hold)">' + h(r.iv_note) + ".</span>" : "")));
    }
    var head = leans ? '<div style="margin-top:4px">A small validated lean where shown; everything grey is a coin flip as far as ' +
      "the evidence goes.</div>" : '<div style="margin-top:4px"><b>Direction uncertain</b>: no horizon shown has a lean the ' +
      "evidence separates from 50%.</div>";
    return head + out.join("");
  }
  // up / sideways / down, probabilities only: a scenario's path is not drawn (a bridge from now into each band
  // would read as a forecast path, and no drift was validated to bend it)
  function scenariosHtml(w, now) {
    var O = (st.board || {}).outlook || {}, S = O.scenarios || [], left_s = w.close_ts - now;
    if (!S.length) return "";
    var D = O.band_frac && liveLevel() ? dist(liveLevel() * O.band_frac) : "one meaningful move";
    var seg = function (v, col, lab) {
      return '<div style="width:' + (100 * v).toFixed(1) + "%;background:" + col + ';line-height:20px;text-align:center;' +
        'white-space:nowrap;overflow:hidden">' + (v >= 0.12 ? lab + " " + (100 * v).toFixed(0) + "%" : "") + "</div>";
    };
    var shown = S.filter(function (x) { return x.key !== "close" ? x.h_s <= left_s : st.tab === "m15"; });
    if (!shown.length) return "";
    var rows = shown.map(function (x) {
      var lab = x.key === "close" ? "To the 15-minute close (" + x.h_s / 60 + " min)" : "Next " + x.h_s / 60 + " min";
      return '<div style="margin:8px 0 2px"><div class="row-between" style="font-size:0.85rem;gap:4px"><span>' + h(lab) + "</span>" +
        chip(x.source === "validated" ? (x.which_way ? "validated: how far and which way" : "validated: how far only") :
             x.tested ? "random walk (the model did not beat it)" : "random walk (not yet tested)", x.source === "validated") + '</div><div style="display:flex;height:20px;border-radius:5px;overflow:hidden;font-size:0.74rem;' +
        'font-weight:600">' + seg(x.up, "rgba(52,211,153,0.55)", "Up") + seg(x.sideways, "rgba(149,161,179,0.3)", "Sideways") +
        seg(x.down, "rgba(248,113,113,0.55)", "Down") + '</div><div class="sub" style="font-size:0.76rem">Up ' + pct(x.up) +
        " · sideways " + pct(x.sideways) + " · down " + pct(x.down) + (x.source === "validated" && !x.which_way ?
        ": the model is validated on how far it moves, not which way, so up and down are split evenly" : "") + "</div></div>";
    }).join("");
    var sup = shown.filter(function (x) { return x.source === "validated" && x.which_way; }).map(function (x) {
      return x.key === "close" ? "the move to the 15-minute close" : "the next " + x.h_s / 60 + " minutes";
    });
    return '<div class="card" style="margin:8px 0;padding:10px 12px"><b>Up, sideways or down</b> <span class="sub">' +
      "sideways = ending within " + h(D) + " of the price now (one meaningful move)</span>" + rows +
      '<p class="sub" style="margin:4px 0">' + (sup.length ? "Which way (a recovery or a decline beyond the random walk's own " +
        "odds) is supported by held-out evidence only for " + h(sup.join(" and ")) + "; it is a small effect." :
        shown.some(function (x) { return x.source === "validated"; }) ? "Which way it goes is not supported by evidence here " +
        "beyond a coin flip: the validated part is how far it moves." : "No scenario model beat the random walk here: the walk's " +
        "own odds are shown.") +
      " Probabilities only: no scenario path is drawn, because a drawn path reads as a prediction.</p></div>";
  }

  // ---- the final-reversal card (the 15-minute market) ----
  function finalHtml() {
    var d = st.board || {}, F = d.final;
    if (st.tab !== "m15") return '<p class="sub" style="margin:4px 0">The final-reversal figures are measured for the 15-minute window only.</p>';
    if (!F) return "";
    var mo = F.model || {}, nu = F.null || {}, M = F.meaningful || {}, m = d.m15 || {};
    var row = function (label, p, validated, tag) {
      return '<tr style="border-top:1px solid var(--border)"><td style="padding:3px 0">' + label + "<br>" +
        chip(tag || (validated ? "validated on held-out windows" : "random walk, unvalidated"), validated, true) +
        '</td><td style="text-align:right;white-space:nowrap;vertical-align:top;padding:3px 0 3px 8px"><b>' + pct(p) + "</b></td></tr>";
    };
    var pick = function (k) { return mo[k] != null ? [mo[k], true] : [nu[k], false]; };
    var fl = pick("final_low"), fh = pick("final_high"), tc = pick("touch");
    var n10 = ((F.round_trip || {}).yes || {}).contracts;
    var mv = dist(M.move_usd);
    return '<div class="card" style="margin:8px 0;padding:10px 12px"><b>Will it turn again before the close?</b>' +
      '<p class="sub" style="margin:4px 0">' + (F.leg === "up" ? "Rising from this window's low of " + h(px(F.window_low)) :
        "Falling from this window's high of " + h(px(F.window_high))) + ". A <b>meaningful</b> move here is " + h(mv) +
      " (" + h(M.basis || "2 one-minute SDs") + ")" + (M.yes_c_per_contract != null ? ", worth about " + num(M.yes_c_per_contract, 1) +
      "¢ a contract on this market's YES now" + (n10 ? ", about " + usd(n10 * M.yes_c_per_contract) + " on a $10 position" : "") : "") +
      ". A new low a few cents past the old one is not a reversal.</p>" +
      '<div style="overflow-x:auto"><table style="width:100%;font-size:0.85rem">' +
      row("The low may have been the final one: no new low of any size", fl[0], fl[1]) +
      row("Another meaningful dip: a new low at least " + h(mv) + " under " + h(px(F.window_low)), M.new_low_walk, false) +
      row("The high may have been the final one: no new high of any size", fh[0], fh[1]) +
      row("Another meaningful peak: a new high at least " + h(mv) + " over " + h(px(F.window_high)), M.new_high_walk, false) +
      row("Crossing the target again before the close", tc[0], tc[1]) +
      row("This move lasting to the close (no meaningful swing against it)", F.persist_to_close, false) +
      '<tr style="border-top:1px solid var(--border)"><td style="padding:3px 0">Meaningful swings left (expected)<br>' +
      chip("random walk scaled to the measured swing rate", false, true) + '</td><td style="text-align:right;vertical-align:top;padding:3px 0 3px 8px"><b>' +
      num(F.swings_expected, 1) + "</b></td></tr></table></div>" +
      '<p class="sub" style="margin:4px 0">These are chances, not timings: nothing on the chart marks a predicted turn, and ' +
      "no buy or sell is ever placed at one.</p></div>";
  }

  // ---- Beginner Mode (the default): dollars, plain English, the owner's Quick-Order workflow ----
  function warning() {
    var R = st.research || {}, r = (st.rec || {}).record || {}, hy = ((R.hybrid || {}).heldout || {}).S4 || {}, z4 = (R.zero_delay_heldout || {}).S4 || {};
    var losing = r.same_rows_model_logloss != null && r.same_rows_model_logloss > r.same_rows_kalshi_logloss;
    return '<div class="card" style="margin:8px 0;padding:10px 12px;border:2px solid var(--no)"><b>No strategy has shown a profit ' +
      "out of sample after fees and spreads.</b>" + '<p class="sub" style="margin:4px 0;color:var(--text)">' +
      (losing ? "Vigil's settlement model is losing to Kalshi's own price on the live record. " : "") +
      "On held-out windows the best rule tested (a time-aware hold / cash-out / switch rule) made " + (hy.per_window_c == null ? "–" :
        usd(hy.per_window_c) + " a window ± " + usd(hy.se_c)) + ", indistinguishable from not trading" +
      (z4.per_window_c == null ? "" : "; filled instantly at the very quote it saw it would have made " + usd(z4.per_window_c) +
        " ± " + usd(z4.se_c) + " a window -- a ceiling no real order reaches, and the edge was gone a minute later") +
      ". Every other rule lost. Paper only.</p></div>";
  }
  function beginnerHtml() {
    var d = st.board || {}, w = win(), F = d.final;
    if (!w) return warning();
    var r = w.target || {}, rt = (st.tab === "m15" && F && F.round_trip) || {};
    var buyLine = function (side) {
      var x = rt[side];
      if (!x) return "";
      return '<div class="stats"><b>$10 of ' + h(side.toUpperCase()) + "</b> buys " + h(x.contracts) + " contracts for " + usd(x.cost_c) +
        " (fee included); they pay " + usd(100 * x.contracts) + " if " + h(side.toUpperCase()) + " wins. To cash out at a " +
        "profit the " + h(side.toUpperCase()) + " bid must climb to " + num(x.breakeven_bid_c, 1) + "¢ (+" + num(x.move_c, 1) + "¢ from now).</div>";
    };
    return warning() + '<div class="card" style="margin:8px 0;padding:10px 12px"><b>Paper-buy this market</b> ' +
      '<span class="sub">' + h(w.ticker) + "</span>" +
      '<div class="row-between" style="gap:6px;margin:6px 0;flex-wrap:wrap"><span><label class="sub">Amount $ <input id="cxDollars" ' +
        'type="number" min="1" max="100" step="1" value="10" style="width:64px"></label></span><span>' +
        '<button type="button" class="subtab" data-cx-buy="yes">Paper-buy YES ' + num(r.yes_ask, 0) + '¢</button> ' +
        '<button type="button" class="subtab" data-cx-buy="no">Paper-buy NO ' + num(r.no_ask, 0) + "¢</button></span></div>" +
      '<p class="sub" id="cxBuyMsg" style="margin:2px 0">' + h(st.buyMsg || "") + "</p>" + buyLine("yes") + buyLine("no") +
      '<p class="sub" style="margin:4px 0">Simulated at Kalshi\'s ask when the server reads the market, the taker fee rounded up ' +
      "to the cent. No order is sent anywhere.</p></div>" + finalHtml();
  }

  function setMode(mode) {
    st.mode = mode;
    TECH.forEach(function (id) { var x = el(id); if (x) x.classList.toggle("hidden", mode === "beginner"); });
    var mb = el("cxMode");
    if (mb) {
      mb.innerHTML = [["beginner", "Beginner"], ["advanced", "Advanced"]].map(function (x) {
        return '<button type="button" class="subtab' + (x[0] === mode ? " active" : "") + '" data-cx-mode="' + x[0] + '"' +
          (x[0] === mode ? ' aria-pressed="true"' : "") + ">" + h(x[1]) + "</button>";
      }).join(" ");
    }
    var kb = el("cxKBox");
    if (kb && mode !== "beginner") kb.open = true;
    ctl(); bar();
  }

  // ---- Kalshi's own price for this tab's market, and the model's fair price this session ----
  function drawContract() {
    if (!st.charts) return;
    var C = st.charts, k = st.contract || {}, w = win() || {}, m = w.target || {};
    var cs = k.ticker === w.ticker ? (k.candles || []).filter(function (x) { return x[0]; }) : [];
    var pt = function (i) { return cs.filter(function (x) { return x[i] != null; }).map(function (x) { return {time: x[0], value: x[i]}; }); };
    C.kya.setData(pt(2)); C.kyb.setData(pt(1));
    C.kna.setData(cs.filter(function (x) { return x[1] != null; }).map(function (x) { return {time: x[0], value: 100 - x[1]}; }));
    C.knb.setData(cs.filter(function (x) { return x[2] != null; }).map(function (x) { return {time: x[0], value: 100 - x[2]}; }));
    C.kfair.setData(st.fair.ticker === w.ticker ? st.fair.pts.slice() : []);
    var mk = [];
    ((st.paper || {}).open || []).concat(((st.paper || {}).recent || [])).forEach(function (p) {
      if (p.ticker !== w.ticker) return;
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
      info.textContent = w.ticker ? w.ticker + ": YES bid/ask " + num(m.yes_bid, 0) + "/" + num(m.yes_ask, 0) + "c (spread " +
        num(m.spread_c, 0) + "c, taker fee at the ask about " + num(fee(m.yes_ask), 2) + "c), NO bid/ask " + num(m.no_bid, 0) + "/" +
        num(m.no_ask, 0) + "c, volume " + num(m.volume, 0) + ". Kalshi's one-minute history, the last hour at most. Solid: asks; " +
        "dotted: bids; green YES, red NO; blue: the model's fair YES at each refresh this session (not stored)." : "";
    }
  }

  // ---- paper positions: each with its own value graph ----
  function posHtml(p) {
    var e = p.eval || {}, o = e.other_side || {}, v = p.value || {}, cost = p.entry_c * p.qty + p.entry_fee_c;
    var W = (st.board || {}).windows || {}, q = null;
    Object.keys(W).forEach(function (k) { var x = W[k]; if (x && x.target && x.target.ticker === p.ticker) q = x.target; });
    var depth = q ? (p.side === "yes" ? q.yes_bid_size : q.yes_ask_size) : null;     // NO's bid is YES's ask
    return '<div style="border-top:1px solid var(--border);padding:6px 0"><div class="row-between" style="flex-wrap:wrap;gap:4px"><span><b>' +
      h(p.side.toUpperCase()) + "</b> x" + h(p.qty) + " " + h(p.ticker) + " @ " + num(p.entry_c, 0) + "c (+" +
      num(p.entry_fee_c, 0) + "c fee) · bought " + h(mt(p.entry_ts)) + "</span>" + '<button type="button" class="subtab" data-cx-close="' +
      h(p.id) + '">Paper-sell at the bid</button></div>' +
      (v.points && v.points.length ? '<div style="color:var(--muted);margin:4px 0">' + svgValue(v, 600, 90) + "</div>" : "") +
      '<div class="stats">Spent <b>' + usd(cost) + "</b> (fee in; the dashed line is break-even) · cash out now (bid " + num(e.bid_c, 0) +
      "¢, after the fee) <b>" + usd(e.close_value_c) + "</b> · net P/L <b>" + usd(e.unrealized_c) + " (" +
      (e.unrealized_c == null ? "–" : num(100 * e.unrealized_c / cost, 1)) + "%)</b></div>" +
      '<div class="stats">Since entry: high ' + usd(v.high_c) + ", low " + usd(v.low_c) + " · pays " + usd(v.payout_c) +
      " if right at the close · holding instead of selling now: up to " + usd(v.upside_c) + " more, or " + usd(v.downside_c) +
      " less if it settles wrong · " + h(left(p.tau_s)) + " left · the side's chance now " + pct(e.p_side) + " (at entry " + pct(p.p_entry) + ")</div>" +
      (depth != null && depth < p.qty ? '<div class="stats" style="color:var(--hold)">The bid shows only ' + h(num(depth, 0)) +
        " contracts: selling all " + h(p.qty) + " would walk down the book.</div>" : "") +
      '<details data-k="pos' + h(p.id) + '"><summary class="sub">hold, sell or switch, by the model</summary><div class="stats">By the model: hold worth ' +
      num(e.hold_value_model_c, 0) + "c, sell " + num(e.close_value_c, 0) + "c → <b>" + h(e.verdict_by_model || "–") +
      "</b>. By Kalshi's own mid: hold worth " + num(e.hold_value_kalshi_mid_c, 0) + "c. Buying " + h(String(o.side || "").toUpperCase()) +
      " instead is its own decision: " + (o.edge_c == null ? "no ask" : (o.edge_c > 0 ? "+" : "") + num(o.edge_c, 1) + "c fee-in edge at " +
        num(o.ask_c, 0) + "c, " + (o.worth_it_by_model ? "clears" : "does not clear") + " the 3c rule") + ".</div>" +
      '<p class="sub" style="margin:2px 0">' + h(e.note || "") + " " + h(v.basis || "") + ". In the research's held-out test a " +
      "decision filled one minute late, and that delay erased the edge.</p></details></div>";
  }
  function paperHtml() {
    var P = st.paper, w = win(), m = w && w.target;
    var head = '<div class="card" style="margin:8px 0;padding:10px 12px"><b>Paper positions</b> <span class="chip">paper only</span>' +
      '<p class="sub" style="margin:4px 0">Buy at the ask, sell at the bid, the taker fee on each order rounded up to the cent; ' +
      "held to the close it settles at Kalshi's result with no fee. No order is sent anywhere.</p>" +
      (st.mode !== "beginner" && m && m.ticker ? '<div class="row-between" style="gap:6px;flex-wrap:wrap"><span class="sub">' + h(m.ticker) + '</span><span>' +
        '<input id="cxQty" type="number" min="1" max="100" value="1" style="width:64px" aria-label="contracts"> ' +
        '<button type="button" class="subtab" data-cx-open="yes">Paper-buy YES ' + num(m.yes_ask, 0) + 'c</button> ' +
        '<button type="button" class="subtab" data-cx-open="no">Paper-buy NO ' + num(m.no_ask, 0) + "c</button></span></div>" : "") +
      '<p class="sub" id="cxPaperMsg" style="margin:4px 0">' + h(st.paperMsg || "") + "</p>";
    if (!P) return head + "</div>";
    if (P.error) return head + '<p class="sub">' + h(P.error) + "</p></div>";
    var open = (P.open || []).map(posHtml).join("");
    var done = (P.recent || []).slice(0, 8).map(function (x) {
      return "<li>" + h(x.side.toUpperCase()) + " x" + h(x.qty) + " " + h(x.ticker) + ": bought " + h(mt(x.entry_ts)) + " at " +
        num(x.entry_c, 0) + "¢, " + (x.status === "settled" ? "settled " + h(String(x.result || "").toUpperCase()) : "sold " +
        h(mt(x.exit_ts)) + " at " + num(x.exit_c, 0) + "¢") + " · <b>" + usd(x.pnl_c) + "</b></li>";
    }).join("");
    var r = P.record || {};
    return head + (open || '<p class="sub">No open paper positions.</p>') +
      (done ? '<details data-k="done" style="margin-top:4px"><summary class="sub">Recent closed and settled</summary><ul style="margin:4px 0 0 18px;padding:0">' +
        done + "</ul></details>" : "") +
      '<div class="stats" style="margin-top:6px">Closed and settled: ' + h(r.positions) + " positions, " +
      usd(r.pnl_c) + " after " + usd(r.fees_c) + " of fees" +
      (r.positions ? " (" + num(r.per_position_c, 1) + "c a position ± " + num(r.se_c, 1) + ", win rate " + pct(r.win_rate) + ")" : "") +
      "</div></div>";
  }

  // ---- Advanced: the research panels ----
  function honesty() {
    var r = (st.rec || {}).record || {}, p = r.paper || {};
    if (r.same_rows_model_logloss == null) return '<p class="sub" style="margin:4px 0">The live record has no graded rows yet.</p>';
    var losing = r.same_rows_model_logloss > r.same_rows_kalshi_logloss;
    return '<div class="card" style="margin:8px 0;padding:8px 12px;border-color:' + (losing ? "var(--no)" : "var(--yes)") + '">' +
      "<b>" + (losing ? "The settlement model is LOSING to Kalshi's price on the live record" :
                        "The settlement model is ahead of Kalshi's price on the live record") + "</b>" +
      (r.decided ? "" : " (not yet decided past 2 SE)") + ". Every signal is unproven until this changes." +
      '<details data-k="nums"><summary class="sub">the numbers</summary><div class="stats">log loss ' + num(r.same_rows_model_logloss, 4) +
      " vs Kalshi " + num(r.same_rows_kalshi_logloss, 4) + " over " + h(r.priced) + " rows · paper rule " + h(p.bets || 0) +
      " bets at " + (p.per_bet_c == null ? "–" : (p.per_bet_c > 0 ? "+" : "") + num(p.per_bet_c, 2) + "c ± " + num(p.se_per_bet_c, 2)) +
      "</div></details></div>";
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
      '<div class="row-between"><b>15 minutes: the numbers</b><span class="sub">closes ' + h(mt(r.close_ts)) + " · " +
      h(left(r.tau_s)) + "</span></div>" +
      '<p class="sub" style="margin:4px 0">Settles on: ' + says + ".</p>" +
      (r.in_window ? '<p class="sub" style="margin:4px 0;color:var(--hold)">Inside the settlement window: the seconds ' +
        "already averaged are taken at the current level (an approximation; the record never logs here).</p>" : "") +
      '<div class="stats">Model <b>' + pct(r.p_model) + "</b>" + h(band) + " · Kalshi bid/ask <b>" + num(r.yes_bid, 1) +
      "/" + num(r.yes_ask, 1) + "c</b> (mid " + num(r.kalshi_mid, 1) + ") · implied vol <b>" + ivCell(r) + "</b></div>" +
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
      "</b> (a random walk's crossing probability; touching is not settling on the other side).</div></div>";
  }
  function momentum() {
    var box = el("cxMomentum"), d = st.board;
    if (!box || !d || d.off || st.mode === "beginner") return;
    var g = st.gran, bars = (LIVE_ONLY[g] ? (st.bars[g] || []) : candleData(st.trail, st.bars[60] || [], 60, 60, -Infinity, Infinity)).slice(-200);
    if (!LIVE_ONLY[g]) g = 60;
    var lv = liveLevel(), sg = d.sigma_s, m = d.m15;
    if (bars.length < 30 || !lv || !sg) { box.innerHTML = '<p class="sub">Momentum: waiting for data.</p>'; return; }
    var s = series(bars), n = bars.length - 1, s1 = lv * sg * Math.sqrt(g), gl = g < 60 ? g + "s" : g / 60 + "m";
    var z = (s.e9[n] - s.e21[n]) / s1, z3 = (s.e9[n - 3] - s.e21[n - 3]) / s1;
    var state = z > 0.5 ? "bullish" : z < -0.5 ? "bearish" : "neutral";
    var trend = Math.abs(z) > Math.abs(z3) ? "strengthening" : "weakening";
    var m5 = (s.cl[n] / s.cl[n - 5] - 1) / (sg * Math.sqrt(5 * g));
    var R = (st.research || {}).direction || {}, D = (d.direction || {}).horizons || {};
    var rev = [1, 3, 5].map(function (hh) {
      var x = R[String(hh)] || {}, now = D[String(hh)] || {};
      var fit = num((x.gain_vs_rw_nats || 0) * 1000, 2) + " ± " + num((x.se_days || 0) * 1000, 2) + " millinats a minute";
      if (x.passed && now.p_up != null) {
        return "<li>" + hh + " min: up <b>" + pct(now.p_up) + "</b>" + (now.p_reversal == null ? "" :
          ", against the last 5 minutes <b>" + pct(now.p_reversal) + "</b>") + " (passed its held-out test, " + fit + " over the random walk)</li>";
      }
      return "<li>" + hh + " min: <b>50% (random walk)</b>; " + (x.passed ? "model unavailable right now" :
        "no indicator beat the random walk on the held-out fortnight (best " + h(x.chosen || "?") + ", " + fit + ")") + "</li>";
    }).join("") + "<li>The tilt did not become a settlement edge, and trading on it inside the window lost " +
      num((((st.research || {}).intrawindow || {}).heldout || {}).per_trade_c, 2) + "c a trade on held-out quotes.</li>";
    var dist = m && m.rule ? lv - m.rule.strike : null;
    var bk = st.book && st.book.bs + st.book.as > 0 ? (st.book.bs - st.book.as) / (st.book.bs + st.book.as) : null;
    box.innerHTML = '<div class="card" style="margin:8px 0;padding:10px 12px"><b>Momentum and reversal: diagnostics</b> ' +
      '<span class="sub">trailing history, not cut to the window</span>' +
      '<div class="stats" style="margin:4px 0">State <b>' + h(state) + "</b> (EMA 9 vs 21 on the " + h(gl) + " bars: " + num(z, 2) +
      " bar SDs, " + h(trend) + ") · last 5 bars " + num(m5, 2) + " SDs · RSI " + num(s.rsi[n], 0) + " · MACD histogram " +
      (s.hist[n] >= 0 ? "positive" : "negative") + (bk == null ? "" : " · top-of-book imbalance " + num(bk, 2) + " (untested)") + "</div>" +
      '<div class="stats">Forecasts by horizon: <ul style="margin:2px 0 2px 18px;padding:0">' + rev + "</ul></div>" +
      '<div class="stats">Distance to the 15-minute strike: <b>' + (dist == null ? "–" : (dist > 0 ? "+" : "") + h(px(dist))) + "</b>" +
      (m && m.z != null ? " (" + num(m.z, 2) + " SDs of the move left)" : "") + "</div></div>";
  }
  function hourlyTable(hr, proven) {
    if (!hr || !hr.markets || !hr.markets.length) return '<p class="sub">No hourly above/below ladder open.</p>';
    var rows = hr.markets.map(function (r) {
      return "<tr><td>" + h(px(r.rule && r.rule.strike)) + '</td><td style="text-align:right">' + pct(r.p_model) +
        '</td><td style="text-align:right">' + num(r.yes_bid, 0) + "/" + num(r.yes_ask, 0) +
        '</td><td style="text-align:right">' + pct(r.p_cross) + '</td><td style="text-align:right">' +
        edgeCell(r.edge, "yes", proven) + '</td><td style="text-align:right">' + edgeCell(r.edge, "no", proven) + "</td></tr>";
    }).join("");
    return '<details data-k="ladder" class="card" style="margin:8px 0;padding:10px 12px"><summary><b>Hourly ladder</b> <span class="sub">' + h(hr.event) +
      " · closes " + h(mt(hr.close_ts)) + "</span></summary>" +
      '<p class="sub" style="margin:4px 0">The nine strikes nearest the level' + (hr.unpriced ? "; " + h(hr.unpriced) +
      " market(s) whose rule did not parse are left out" : "") + ".</p>" +
      '<div style="overflow-x:auto"><table style="width:100%;font-size:0.85rem;font-variant-numeric:tabular-nums">' +
      '<tr style="color:var(--muted)"><th style="text-align:left">strike</th><th style="text-align:right">model</th>' +
      '<th style="text-align:right">bid/ask</th><th style="text-align:right" title="chance the level touches the strike before the close">touch</th>' +
      '<th style="text-align:right">YES edge</th><th style="text-align:right">NO edge</th></tr>' +
      rows + "</table></div>" + (proven ? "" : '<p class="sub" style="margin:4px 0">Edges are unproven.</p>') + "</details>";
  }
  function proxyLine(d) {
    var p = d.proxy || {};
    var src = (p.sources || []).map(function (s) {
      return '<span class="chip" title="' + h(s.ok ? (s.age_s != null ? s.age_s + " s old" : "no timestamp") +
        (s.stale ? " - stale, left out" : "") : (s.error || "unavailable")) + '">' + h(s.source) + " " +
        (s.ok ? h(px(s.mid)) + (s.dev_bps != null ? " (" + (s.dev_bps > 0 ? "+" : "") + h(num(s.dev_bps, 1)) + " bp)" : "") : "✗") +
        "</span>";
    }).join(" ");
    return '<div class="stats" style="margin:4px 0">Index proxy <b>' + h(px(p.price)) + "</b> (" + h(p.basis || "") + ") · " +
      "realised vol <b>" + num(d.sigma_ann_pct, 0) + "%</b> a year</div><div>" + src + "</div>";
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
    return '<details data-k="record" class="card" style="margin:8px 0;padding:10px 12px"><summary><b>Research: the live record</b> <span class="sub">' +
      h(call) + "</span>" + (small ? ' <span class="chip">small sample</span>' : "") + "</summary>" +
      '<div class="stats" style="margin:4px 0">Settlement, at the decision look (15-minute 5 min, hourly 15 min before the close): ' +
      h(r.priced) + " rows priced both ways over " + h(r.days) + " day(s) · log loss model <b>" + num(r.same_rows_model_logloss, 4) +
      "</b> vs Kalshi <b>" + num(r.same_rows_kalshi_logloss, 4) + "</b> (difference " + num(r.diff_logloss, 4) + " ± " +
      num(r.diff_se, 4) + ")</div>" +
      '<div class="stats">Paper rule (' + h(p.rule || "") + "): " + h(p.bets || 0) + " bets, " +
      (p.per_bet_c == null ? "–" : (p.per_bet_c > 0 ? "+" : "") + num(p.per_bet_c, 2) + "c a bet ± " + num(p.se_per_bet_c, 2)) +
      ", worst drawdown " + num(p.max_drawdown_c, 0) + "c.</div>" +
      (reg ? '<div class="stats">By volatility, model vs Kalshi log loss: ' + reg + "</div>" : "") +
      (r.proxy_rms_bps != null ? '<div class="stats">Proxy level a minute or more before the close vs the official average: ' +
        num(r.proxy_rms_bps, 1) + " bp RMS over " + h(r.proxy_n) + " markets (mostly the walk's own last-minute move, not " +
        "proxy error: the same minute measured 1.9-3.7 bp in research)</div>" : "") +
      '<div class="stats">Trading inside the window: ' + (iw.trades ? h(iw.rule) + ": " + h(iw.trades) + " trades, " +
        (iw.per_trade_c > 0 ? "+" : "") + num(iw.per_trade_c, 2) + "c a trade ± " + num(iw.se_c, 2) + ", total " +
        num(iw.total_c, 0) + "c, worst drawdown " + num(iw.max_drawdown_c, 0) + "c" : "no completed trades yet") + "</div>" +
      predsHtml(x.preds, x.book) +
      (by ? '<div style="overflow-x:auto"><table style="width:100%;font-size:0.82rem"><tr style="color:var(--muted)"><th>coin</th>' +
        '<th>contract</th><th style="text-align:right">look</th><th style="text-align:right">rows</th>' +
        '<th style="text-align:right">model LL</th><th style="text-align:right">Kalshi LL</th></tr>' + by + "</table></div>" : "") + "</details>";
  }
  // the forecasts' own live record, kept apart from any money record (the owner: predicting another dip is a
  // different achievement from trading on it profitably)
  function predsHtml(P, B) {
    P = P || {};
    var names = {direction_1: "direction, 1 min", direction_3: "direction, 3 min", direction_5: "direction, 5 min",
                 final_low: "final low", final_high: "final high", touch: "touch the target again"};
    var rows = Object.keys(names).map(function (k) {
      var x = P[k] || {};
      if (!x.n) return "";
      return "<li>" + h(names[k]) + " (" + h(x.vs) + "): " + num(1000 * x.diff_nats, 1) + " ± " + num(1000 * (x.se || 0), 1) +
        " millinats over " + h(x.n) + " forecasts" + (x.better ? " · <b>better</b>" : x.worse ? " · <b>worse</b>" : " · not decided") + "</li>";
    }).join("");
    B = B || {};
    return '<div class="stats" style="margin-top:6px"><b>The forecasts</b> (logged at each look before the outcome, graded after ' +
      "the close; negative is better)" + (rows ? '<ul style="margin:4px 0 4px 18px;padding:0">' + rows + "</ul>" : ": none graded yet. ") +
      "Kalshi book recorder (" + (B.recorded_on === "server" ? "on the server" :
        "on the PC, uploaded every 3 minutes: when the PC is off nothing is recorded") + "): " + h(B.rows || 0) +
      " snapshots over " + num(B.days || 0, 1) + " day(s)" + (B.last_ts ? ", the newest " + h(left(Date.now() / 1000 - B.last_ts)) +
      " ago" : "") + "; the 5/15/30/60-second delay test reads it once after " + h(B.rescore_after_days || 14) + " days.</div>";
  }

  // ---- controls ----
  function tabs() {
    var b = el("cxTabs"), W = (st.board || {}).windows || {};
    if (!b) return;
    b.innerHTML = TABS.map(function (t) {
      var w = W[t[0]], on = t[0] === st.tab;
      return '<button type="button" role="tab" class="subtab' + (on ? " active" : "") + '" data-cx-tab="' + t[0] + '" aria-selected="' +
        (on ? "true" : "false") + '">' + h(t[1]) + (w ? ' <span style="font-weight:400;opacity:0.8">· ' + h(mtFmt(w.close_ts)) + "</span>" :
        st.board ? ' <span style="font-weight:400;opacity:0.7">· none open</span>' : "") + "</button>";
    }).join(" ");
  }
  function ctl() {
    var b = el("cxCtl"), w = win();
    if (!b) return;
    var btn = function (k, t, on) {
      return '<button type="button" class="subtab' + (on ? " active" : "") + '" data-cx-ctl="' + k + '">' + h(t) + "</button>";
    };
    var canPrev = st.tab !== "daily" && !!(w && w.previous && w.previous.open_ts);
    b.innerHTML = "<span>" + btn("line", "Line", st.style === "line") + " " + btn("candles", "Candles", st.style === "candles") +
      '</span><span class="sub"><label style="display:inline-flex;flex-direction:row;gap:4px;align-items:center;margin-right:10px"><input type="checkbox" data-cx-show="prev"' + (st.prev ? " checked" : "") +
      (canPrev ? "" : " disabled") + '> Previous window</label><label style="display:inline-flex;flex-direction:row;gap:4px;align-items:center">' +
      "Forecast band <select data-cx-band aria-label=\"forecast band\" style=\"width:auto;display:inline-block;padding:2px 6px;margin-left:4px;min-height:0\">" + [[0, "off"], [50, "50%"], [80, "80%"],
        [95, "95%"]].map(function (x) {
        return '<option value="' + x[0] + '"' + (x[0] === st.band ? " selected" : "") + ">" + x[1] + "</option>";
      }).join("") + "</select></label></span><span>" + btn("reset", "Reset Zoom") + " " +
      btn("auto", "Auto Scale") + " " + btn("fit", "Fit Current Market") + "</span>";
  }
  function bar() {
    var b = el("cxChartBar");
    if (!b) return;
    var tog = [["ema", "EMA 5/9/21"], ["vwap", "VWAP"], ["bb", "Bollinger"], ["sr", "window range"]];
    b.innerHTML = '<span class="sub">Candles: ' + ADV_GRANS.map(function (g) {
      return '<button type="button" class="subtab' + (g[0] === st.gran ? " active" : "") + '" data-cx-gran="' + g[0] + '"' +
        (g[0] === st.gran ? ' aria-pressed="true"' : "") + ">" + h(g[1]) + "</button>";
    }).join(" ") + "</span><span>" + tog.map(function (t) {
      return '<label class="sub" style="margin-left:6px"><input type="checkbox" data-cx-show="' + t[0] + '"' +
        (st.show[t[0]] ? " checked" : "") + "> " + h(t[1]) + "</label>";
    }).join("") + "</span>";
  }

  function render(d) {
    st.board = d;
    if (d.off) { el("cxStats").innerHTML = '<p class="sub">' + h(d.note) + "</p>"; return; }
    var w = win();
    var wk = w ? st.tab + "|" + (w.ticker || "") + "|" + w.close_ts : "";
    if (st.winKey && wk !== st.winKey && w && st.lastWin && st.lastWin.tab === st.tab && w.close_ts > st.lastWin.close_ts) {
      st.moved = "The " + mt(st.lastWin.close_ts) + " market closed; now showing the next one (" + mt(w.open_ts) + " to " + mt(w.close_ts) +
        "). Settled paper positions stay in the list below.";
    }
    st.winKey = wk;
    st.lastWin = w ? {tab: st.tab, close_ts: w.close_ts} : null;
    var m = w && w.target;
    if (m && m.ticker && m.p_model != null) {                 // the model's fair YES this session, capped
      if (st.fair.ticker !== m.ticker) st.fair = {ticker: m.ticker, pts: []};
      var t = Math.floor(d.ts / 60) * 60, pts = st.fair.pts;
      if (pts.length && pts[pts.length - 1].time === t) pts[pts.length - 1].value = 100 * m.p_model;
      else pts.push({time: t, value: 100 * m.p_model});
      if (pts.length > 120) pts.splice(0, pts.length - 120);
    }
    setHTML("cxHonest", st.mode === "beginner" ? "" : honesty());
    setHTML("cxBeginner", beginnerHtml());
    setHTML("cxBoard", proxyLine(d) + m15Card(d.m15, !!d.edges_proven) + whatIf(d.m15) + hourlyTable(d.hourly, !!d.edges_proven));
    setHTML("cxPaper", paperHtml());
    tabs(); ctl(); statsHtml(); nowHtml();
  }

  function load() {
    if (st.busy) { st.again = true; return; }
    st.busy = true;
    st.polls += 1;
    var coin = st.coin || "BTC";
    var adv = st.mode !== "beginner";
    var want = [getJSON("/api/crypto/board?coin=" + encodeURIComponent(coin)),
                st.rec && st.polls % 4 ? Promise.resolve(st.rec) : getJSON("/api/crypto/record"),
                st.research ? Promise.resolve(st.research) : getJSON("/api/crypto/research"),
                getJSON("/api/crypto/paper"),
                // trailing candles for the Advanced indicators and diagnostics (never cut to the window)
                adv && (!st.trail.length || st.polls % 2) ? getJSON("/api/crypto/candles?coin=" + encodeURIComponent(coin) + "&gran=60") :
                  Promise.resolve(null)];
    Promise.all(want).then(function (res) {
      var d = res[0] || {}, rec = res[1] || {};
      if (d.error) throw new Error(d.error);
      st.rec = rec.error ? st.rec : rec;
      st.research = res[2] || st.research;
      st.paper = res[3];
      if (res[4] && res[4].candles) st.trail = res[4].candles.slice(-MAXBARS);
      st.boardAt = Date.now() / 1000;
      st.loadErr = null;
      if (!st.coin) {
        st.coin = d.coin;
        el("cxCoin").innerHTML = (d.coins || []).map(function (k) {
          return '<option value="' + h(k) + '"' + (k === d.coin ? " selected" : "") + ">" + h(k) + "</option>";
        }).join("");
      }
      render(d);
      if (d.symbol && !d.off) openWS(d.symbol);
      setHTML("cxRecord", rec.error ? '<p class="sub">Record unavailable (' + h(rec.error) + ").</p>" : recordHtml(st.rec || {}));
      var w = win();
      if (!w) return [null, null];
      var p = plan(w, st.prev, st.mode, st.gran, st.style), key = coin + "|" + p.rest + "|" + p.start + "|" + p.end;
      return Promise.all([
        getJSON("/api/crypto/candles?coin=" + encodeURIComponent(coin) + "&gran=" + p.rest + "&start=" + p.start + "&end=" + p.end)
          .then(function (c) { return [key, c]; }),
        w.ticker ? getJSON("/api/crypto/contract?coin=" + encodeURIComponent(coin) + "&ticker=" + encodeURIComponent(w.ticker)) : null]);
    }).then(function (res) {
      var c = res && res[0], k = res && res[1];
      if (c && c[1] && c[1].candles) { st.hist = c[1].candles.slice(-MAXPTS); st.histKey = c[0]; }
      st.contract = k && !k.error ? k : null;
      drawPrice(); drawContract(); momentum();
    }).catch(function (err) {
      st.loadErr = String(err && err.message);
      if (!st.board) el("cxStats").innerHTML = '<p class="sub">Could not load the crypto board (' + h(err && err.message) + ").</p>";
      else statsHtml();
    }).then(function () {
      st.busy = false;
      if (st.again) { st.again = false; load(); } else arm();
    });
  }

  function paperMsg(t) { st.paperMsg = t; var x = el("cxPaperMsg"); if (x) x.textContent = t; }
  function buyMsg(t) { st.buyMsg = t; var x = el("cxBuyMsg"); if (x) x.textContent = t; }
  function redraw() { st.gridKey = ""; ctl(); bar(); drawPrice(); nowHtml(); }

  function wire() {
    if (st.wired) return;
    st.wired = true;
    bar(); ctl(); tabs();
    el("cxCoin").addEventListener("change", function (e) {
      st.coin = e.target.value; st.bars = {}; st.hist = []; st.histKey = ""; st.trail = []; st.lastPx = null; st.lastTick = 0;
      st.moved = ""; closeWS(); load();
    });
    el("cxTabs").addEventListener("click", function (e) {
      var b = e.target.closest("[data-cx-tab]");
      if (!b) return;
      st.tab = b.getAttribute("data-cx-tab");
      if (st.tab === "daily") st.prev = false;
      st.moved = ""; st.hist = []; st.histKey = "";
      if (st.board) render(st.board);
      redraw();
      load();
    });
    el("cxCtl").addEventListener("click", function (e) {
      var b = e.target.closest("[data-cx-ctl]");
      if (!b || !st.charts) return;
      var k = b.getAttribute("data-cx-ctl");
      if (k === "line" || k === "candles") { st.style = k; redraw(); load(); }
      else if (k === "auto") st.charts.price.priceScale("right").applyOptions({autoScale: true});
      else if (k === "fit") fit(false);
      else if (k === "reset") fit(true);
      badge();
    });
    var onShow = function (e) {
      if (e.target.hasAttribute("data-cx-band")) { st.band = Number(e.target.value) || 0; drawPrice(); return; }
      var k = e.target.getAttribute("data-cx-show");
      if (!k) return;
      if (k === "prev") { st.prev = !!e.target.checked; redraw(); load(); return; }
      st.show[k] = !!e.target.checked;
      drawPrice();
    };
    el("cxCtl").addEventListener("change", onShow);
    el("cxChartBar").addEventListener("change", onShow);
    el("cxChartBar").addEventListener("click", function (e) {
      var b = e.target.closest("[data-cx-gran]");
      if (!b) return;
      st.gran = Number(b.getAttribute("data-cx-gran"));
      st.style = "candles";
      redraw();
      load();
    });
    el("cxPaper").addEventListener("click", function (e) {
      var o = e.target.closest("[data-cx-open]"), c = e.target.closest("[data-cx-close]");
      var m = (win() || {}).target;
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
    el("cxBeginner").addEventListener("click", function (e) {
      var b = e.target.closest("[data-cx-buy]"), m = (win() || {}).target;
      if (!b || !m || !m.ticker) return;
      var dol = Math.max(1, Math.min(100, Number((el("cxDollars") || {}).value) || 10));
      buyMsg("Reading Kalshi's ask…");
      postJSON("/api/crypto/paper/open", {ticker: m.ticker, coin: st.coin, side: b.getAttribute("data-cx-buy"), dollars: dol})
        .then(function (r) {
          buyMsg(r.error ? r.error : "Paper-bought " + r.qty + " contracts at " + num(r.entry_c, 0) + "¢ for " + usd(r.cost_c) + ".");
          load();
        })
        .catch(function (err) { buyMsg(String(err && err.message)); });
    });
    el("cxMode").addEventListener("click", function (e) {
      var b = e.target.closest("[data-cx-mode]");
      if (b) { setMode(b.getAttribute("data-cx-mode")); if (st.board) render(st.board); redraw(); drawContract(); load(); }
    });
    var kb = el("cxKBox");
    if (kb) kb.addEventListener("toggle", function () { if (st.charts) st.charts.contract.applyOptions({width: boxW()}); });
    document.addEventListener("visibilitychange", function () {
      if (onScreen() && !st.timer) load();
      else if (!onScreen()) closeWS();
    });
  }

  // the pure helpers, for the guards (node) -- no DOM, no state
  window.cxPure = {pickStep: pickStep, plan: plan, gridTimes: gridTimes, lineData: lineData, candleData: candleData,
                   scaleRange: scaleRange, observed: observed, svgValue: svgValue};
  window.initCrypto = function () {
    wire();
    setMode(st.mode);
    loadLib(function () { buildCharts(); if (!st.timer) load(); });
  };
})();
