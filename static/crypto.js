// The crypto terminal (2026-11-27, crypto_terminal.py): Kalshi's 15-minute and hourly crypto contracts priced off
// the rule each settles on, a candle chart, and the paper record. Display only and PAPER ONLY: this script reads
// /api/crypto/* and nothing else, and places nothing. Every server string is escaped (h); no inline handlers;
// one refresh timer, re-armed only while the tab is on screen and the page is visible. Times show in Mountain
// time, the owner's clock.
(function () {
  "use strict";
  var GRANS = [[60, "1m"], [300, "5m"], [900, "15m"], [3600, "1h"]];
  var REFRESH_MS = 15000;
  var st = {coin: null, gran: 60, timer: null, busy: false, wired: false, board: null};

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
  function mt(ts) {
    if (!ts) return "";
    try {
      return new Date(ts * 1000).toLocaleTimeString("en-US", {timeZone: "America/Denver", hour: "numeric",
                                                               minute: "2-digit"}) + " MT";
    } catch (e) {
      return new Date(ts * 1000).toISOString().slice(11, 16) + " UTC";
    }
  }
  function left(s) {
    if (s == null) return "";
    if (s <= 0) return "closed";
    var m = Math.floor(s / 60), r = Math.round(s % 60);
    return m >= 60 ? Math.floor(m / 60) + "h " + (m % 60) + "m" : m + "m " + r + "s";
  }
  function pct(p) { return p == null ? "–" : (100 * p).toFixed(1) + "%"; }

  function arm() {
    if (st.timer) { clearTimeout(st.timer); st.timer = null; }
    if (!onScreen()) return;                // a hidden tab or a backgrounded phone is never polled
    st.timer = setTimeout(function () { st.timer = null; if (onScreen()) load(); }, REFRESH_MS);
  }

  function getJSON(url) {
    return fetch(url).then(function (r) { return r.json(); });
  }

  // ---- the chart: Coinbase candles drawn as inline SVG, the open market's strike and the level on it ----
  function chart(candles, marks) {
    var box = el("cxChart");
    if (!box) return;
    if (!candles || !candles.length) { box.innerHTML = '<p class="sub">No candles.</p>'; return; }
    var W = Math.max(280, Math.min(box.clientWidth || 360, 1100)), H = 220, padL = 4, padR = 62, padT = 8, padB = 18;
    var cs = candles.slice(-Math.max(40, Math.min(160, Math.floor((W - padL - padR) / 4))));
    var lo = Infinity, hi = -Infinity;
    cs.forEach(function (c) { lo = Math.min(lo, c[3]); hi = Math.max(hi, c[2]); });
    (marks || []).forEach(function (m) {
      if (m.y != null && m.y > lo - (hi - lo) && m.y < hi + (hi - lo)) { lo = Math.min(lo, m.y); hi = Math.max(hi, m.y); }
    });
    if (!(hi > lo)) { hi = lo + 1; }
    var span = hi - lo; lo -= span * 0.04; hi += span * 0.04;
    var n = cs.length, cw = (W - padL - padR) / n;
    var y = function (v) { return padT + (hi - v) / (hi - lo) * (H - padT - padB); };
    var parts = [];
    cs.forEach(function (c, i) {
      var x = padL + i * cw + cw / 2, up = c[4] >= c[1];
      var col = up ? "var(--yes)" : "var(--no)";
      parts.push('<line x1="' + x.toFixed(1) + '" x2="' + x.toFixed(1) + '" y1="' + y(c[2]).toFixed(1) + '" y2="' +
                 y(c[3]).toFixed(1) + '" stroke="' + col + '" stroke-width="1"/>');
      var top = y(Math.max(c[1], c[4])), bot = y(Math.min(c[1], c[4]));
      parts.push('<rect x="' + (x - Math.max(1, cw * 0.35)).toFixed(1) + '" y="' + top.toFixed(1) + '" width="' +
                 Math.max(1, cw * 0.7).toFixed(1) + '" height="' + Math.max(1, bot - top).toFixed(1) + '" fill="' + col + '"/>');
    });
    (marks || []).forEach(function (m) {
      if (m.y == null || m.y < lo || m.y > hi) return;
      var yy = y(m.y).toFixed(1);
      parts.push('<line x1="' + padL + '" x2="' + (W - padR) + '" y1="' + yy + '" y2="' + yy + '" stroke="' + m.col +
                 '" stroke-width="1" stroke-dasharray="' + (m.dash || "4 3") + '"/>');
      parts.push('<text x="' + (W - padR + 3) + '" y="' + (Number(yy) + 4) + '" font-size="10" fill="' + m.col + '">' +
                 h(m.label) + "</text>");
    });
    [hi - (hi - lo) * 0.05, (hi + lo) / 2, lo + (hi - lo) * 0.05].forEach(function (v) {
      parts.push('<text x="' + (W - padR + 3) + '" y="' + (y(v) + 4).toFixed(1) + '" font-size="10" fill="var(--faint)">' +
                 h(px(v)) + "</text>");
    });
    parts.push('<text x="' + padL + '" y="' + (H - 4) + '" font-size="10" fill="var(--faint)">' + h(mt(cs[0][0])) +
               '</text><text x="' + (W - padR) + '" y="' + (H - 4) + '" font-size="10" text-anchor="end" fill="var(--faint)">' +
               h(mt(cs[n - 1][0])) + "</text>");
    box.innerHTML = '<svg viewBox="0 0 ' + W + " " + H + '" width="100%" height="' + H + '" role="img" aria-label="' +
      h(st.coin + " candles, Coinbase") + '">' + parts.join("") + "</svg>" +
      '<p class="sub" style="margin:2px 0;font-size:0.75rem">Coinbase Exchange public candles (one exchange; the ' +
      "settlement index is CF Benchmarks', which is licensed). Dashed: the open 15-minute market's strike; dotted: the " +
      "four-exchange proxy level.</p>";
  }

  function bar() {
    var b = el("cxChartBar");
    if (!b) return;
    b.innerHTML = '<span class="stats" id="cxLevel"></span><span>' + GRANS.map(function (g) {
      return '<button type="button" class="subtab" data-cx-gran="' + g[0] + '"' +
        (g[0] === st.gran ? ' aria-pressed="true" style="font-weight:600;border-color:var(--accent)"' : "") + ">" + h(g[1]) + "</button>";
    }).join(" ") + "</span>";
  }

  // ---- the board ----
  function edgeCell(e, side, proven) {
    if (!e || e[side] == null) return "–";
    var v = e[side], cls = v > 0 ? "color:var(--yes)" : "color:var(--muted)";
    return '<span style="' + cls + '" title="' + h((proven ? "" : "unproven: ") + "model chance x 100 minus the ask minus " +
      "Kalshi's taker fee (" + num(e[side + "_fee"], 2) + "c)") + '">' + (v > 0 ? "+" : "") + num(v, 1) + "c</span>";
  }

  // the volatility at which the model gives Kalshi's mid; where none does, the market is reading another level
  function ivCell(r) {
    if (r.implied_vol_ann_pct != null) return num(r.implied_vol_ann_pct, 0) + "%";
    return r.iv_note ? '<span title="' + h(r.iv_note) + '" style="color:var(--hold)">none</span>' : "–";
  }

  function m15Card(r, proven) {
    if (!r) return '<p class="sub">No open 15-minute market.</p>';
    var rule = r.rule;
    var says = rule ? h(rule.says) : "the settlement rule did not parse, so this market is not priced";
    var band = r.p_band ? " (" + pct(r.p_band[0]) + "–" + pct(r.p_band[1]) + " at 0.8x–1.25x the volatility)" : "";
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
      edgeCell(r.edge, "no", proven) + (proven ? "" : ' <span class="chip">unproven</span>') + "</div></div>";
  }

  function hourlyTable(hr, proven) {
    if (!hr || !hr.markets || !hr.markets.length) return '<p class="sub">No hourly above/below ladder open.</p>';
    var rows = hr.markets.map(function (r) {
      return "<tr><td>" + h(px(r.rule && r.rule.strike)) + '</td><td style="text-align:right">' + pct(r.p_model) +
        '</td><td style="text-align:right">' + num(r.yes_bid, 0) + "/" + num(r.yes_ask, 0) +
        '</td><td style="text-align:right">' + ivCell(r) + '</td><td style="text-align:right">' +
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
      '<th style="text-align:right">bid/ask</th><th style="text-align:right" title="the volatility at which the model gives ' +
      "Kalshi's mid\">impl. vol</th><th style=\"text-align:right\">YES edge</th><th style=\"text-align:right\">NO edge</th></tr>" +
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

  function render(d) {
    st.board = d;
    var lv = el("cxLevel");
    if (lv) lv.innerHTML = d.proxy ? "Level <b>" + h(px(d.proxy.price)) + "</b>" : "";
    if (d.off) { el("cxBoard").innerHTML = '<p class="sub">' + h(d.note) + "</p>"; return; }
    var proven = !!d.edges_proven;
    el("cxBoard").innerHTML = proxyLine(d) + m15Card(d.m15, proven) + hourlyTable(d.hourly, proven);
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
    var p = r.paper || {};
    var by = (x.by || []).map(function (b) {
      return "<tr><td>" + h(b.coin) + "</td><td>" + h(b.kind === "m15" ? "15 min" : "hourly") + '</td><td style="text-align:right">' +
        h(b.look_s) + ' s</td><td style="text-align:right">' + h(b.priced) + '</td><td style="text-align:right">' +
        num(b.model_logloss, 3) + '</td><td style="text-align:right">' + num(b.kalshi_logloss, 3) + "</td></tr>";
    }).join("");
    return '<div class="card" style="margin:8px 0;padding:10px 12px"><b>Paper record</b> <span class="sub">at the decision ' +
      "look (15-minute 5 min, hourly 15 min before the close)</span>" +
      (small ? ' <span class="chip">small sample</span>' : "") +
      '<div class="stats" style="margin:4px 0">' + h(r.priced) + " rows priced both ways over " + h(r.days) + " day(s) · " +
      "log loss model <b>" + num(r.same_rows_model_logloss, 4) + "</b> vs Kalshi <b>" + num(r.same_rows_kalshi_logloss, 4) +
      "</b> (difference " + num(r.diff_logloss, 4) + " ± " + num(r.diff_se, 4) + ") · <b>" + h(call) + "</b></div>" +
      '<div class="stats">Paper rule (' + h(p.rule || "") + "): " + h(p.bets || 0) + " bets, " +
      (p.per_bet_c == null ? "–" : (p.per_bet_c > 0 ? "+" : "") + num(p.per_bet_c, 2) + "c a bet ± " + num(p.se_per_bet_c, 2)) +
      ", worst drawdown " + num(p.max_drawdown_c, 0) + "c. Paper: no money moved.</div>" +
      (r.proxy_rms_bps != null ? '<div class="stats">Proxy level a minute before the close vs the official average: ' +
        num(r.proxy_rms_bps, 1) + " bp RMS over " + h(r.proxy_n) + " markets</div>" : "") +
      (by ? '<details style="margin-top:6px"><summary class="sub">By coin, contract and look</summary>' +
        '<div style="overflow-x:auto"><table style="width:100%;font-size:0.82rem"><tr style="color:var(--muted)"><th>coin</th>' +
        '<th>contract</th><th style="text-align:right">look</th><th style="text-align:right">rows</th>' +
        '<th style="text-align:right">model LL</th><th style="text-align:right">Kalshi LL</th></tr>' + by + "</table></div></details>" : "") +
      "</div>";
  }

  function load() {
    if (st.busy) return;
    st.busy = true;
    var coin = st.coin || "BTC";
    Promise.all([
      getJSON("/api/crypto/board?coin=" + encodeURIComponent(coin)),
      getJSON("/api/crypto/candles?coin=" + encodeURIComponent(coin) + "&gran=" + st.gran),
      getJSON("/api/crypto/record")
    ]).then(function (res) {
      var d = res[0] || {}, c = res[1] || {}, rec = res[2] || {};
      if (d.error) throw new Error(d.error);
      if (!st.coin) {
        st.coin = d.coin;
        var sel = el("cxCoin");
        sel.innerHTML = (d.coins || []).map(function (k) {
          return '<option value="' + h(k) + '"' + (k === d.coin ? " selected" : "") + ">" + h(k) + "</option>";
        }).join("");
      }
      render(d);
      var marks = [];
      if (d.m15 && d.m15.rule) marks.push({y: d.m15.rule.strike, col: "var(--hold)", label: "15m strike"});
      if (d.proxy && d.proxy.price) marks.push({y: d.proxy.price, col: "var(--accent)", label: "level", dash: "1 3"});
      chart(c.candles || [], marks);
      el("cxRecord").innerHTML = rec.error ? '<p class="sub">Record unavailable (' + h(rec.error) + ").</p>" : recordHtml(rec);
    }).catch(function (err) {
      el("cxBoard").innerHTML = '<p class="sub">Could not load the crypto board (' + h(err && err.message) + ").</p>";
    }).then(function () {
      st.busy = false;
      arm();
    });
  }

  function wire() {
    if (st.wired) return;
    st.wired = true;
    bar();
    el("cxCoin").addEventListener("change", function (e) { st.coin = e.target.value; load(); });
    el("cxChartBar").addEventListener("click", function (e) {
      var b = e.target.closest("[data-cx-gran]");
      if (!b) return;
      st.gran = Number(b.getAttribute("data-cx-gran"));
      bar();
      load();
    });
    document.addEventListener("visibilitychange", function () { if (onScreen() && !st.timer) load(); });
  }

  window.initCrypto = function () {
    wire();
    if (!st.timer) load();
  };
})();
