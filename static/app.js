"use strict";

const $ = (id) => document.getElementById(id);

// True while the user is typing in a field or has an inline form open, so
// auto-refresh can skip a beat instead of wiping what they're entering.
function uiBusy() {
  const a = document.activeElement;
  if (a && ["INPUT", "SELECT", "TEXTAREA"].includes(a.tagName)) return true;
  if (document.querySelector(".buyform:not(.hidden)")) return true;
  return false;
}

// ---- Kelly bet sizing -----------------------------------------------------
function getBankroll() { return parseFloat(localStorage.getItem("bankroll")) || 0; }
function getKellyMult() { const v = localStorage.getItem("kellyMult"); return v == null ? 0.5 : parseFloat(v); }
function kellyFraction(prob, costCents) {
  if (prob == null || costCents == null || costCents <= 0 || costCents >= 100) return 0;
  return Math.max(0, (100 * prob - costCents) / (100 - costCents));
}
// Returns a sizing string for a bet, or "" if there's no edge / no bankroll set.
function stakeText(prob, costCents) {
  const bank = getBankroll(), mult = getKellyMult();
  if (!bank || !mult) return "";
  const f = kellyFraction(prob, costCents) * mult;
  if (f <= 0) return "";
  const dollars = bank * f;
  const contracts = Math.floor(dollars / (costCents / 100));
  if (contracts < 1) return "";
  const label = mult === 1 ? "full" : mult === 0.25 ? "¼" : "½";
  return `💰 Stake <b>$${dollars.toFixed(2)}</b> (~${contracts} @ ${costCents}¢, ${label}-Kelly)`;
}

// ---- Close-time helpers ---------------------------------------------------
function nextMark(minutes) {
  // Next clock boundary (e.g. next :00/:15/:30/:45 for 15).
  const now = new Date();
  const ms = minutes * 60 * 1000;
  const next = new Date(Math.ceil(now.getTime() / ms) * ms);
  if (next.getTime() - now.getTime() < 20 * 1000) next.setTime(next.getTime() + ms);
  return Math.floor(next.getTime() / 1000);
}
function nextHour() {
  const d = new Date();
  d.setMinutes(0, 0, 0);
  d.setHours(d.getHours() + 1);
  return Math.floor(d.getTime() / 1000);
}
function computeCloseTime() {
  const w = $("window").value;
  if (w === "next15") return nextMark(15);
  if (w === "nexthour") return nextHour();
  if (w === "min15") return Math.floor(Date.now() / 1000) + 15 * 60;
  if (w === "min60") return Math.floor(Date.now() / 1000) + 60 * 60;
  if (w === "custom") {
    const v = $("customTime").value;
    if (!v) return null;
    return Math.floor(new Date(v).getTime() / 1000);
  }
  return null;
}

function fmtCountdown(secs) {
  if (secs <= 0) return "closed";
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  if (m >= 60) {
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  }
  return `${m}m ${s}s`;
}
function fmtClock(epoch) {
  return new Date(epoch * 1000).toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function badge(rec, strength) {
  const cls = rec === "BUY YES" ? "yes" : rec === "BUY NO" ? "no" : "hold";
  const lean = strength === "lean" ? " lean" : "";
  const tag = strength === "lean" ? " (lean)" : strength === "strong" ? " ★" : "";
  return `<span class="badge ${cls}${lean}">${rec}${tag}</span>`;
}

// ---- Live preview in the setup card --------------------------------------
let previewTimer = null;
async function refreshPreview() {
  const threshold = parseFloat($("threshold").value);
  const close = computeCloseTime();
  const prev = $("preview");
  if (!threshold || !close) { prev.classList.add("hidden"); return; }
  const params = new URLSearchParams({
    coin: $("coin").value,
    threshold,
    direction: $("direction").value,
    close_time: close,
  });
  const yp = $("yesPrice").value;
  if (yp) params.set("yes_price_cents", yp);
  try {
    const r = await fetch("/api/quote?" + params.toString());
    const sig = await r.json();
    if (sig.error) { prev.classList.add("hidden"); return; }
    prev.classList.remove("hidden");
    prev.innerHTML = renderSignalBody(sig, close, null);
  } catch (e) {
    prev.classList.add("hidden");
  }
}

// ---- Shared signal renderer ----------------------------------------------
function renderSignalBody(sig, closeTime, marketId) {
  const pctYes = Math.round(sig.prob_yes * 100);
  const secs = closeTime - Math.floor(Date.now() / 1000);
  const edgeTxt = sig.edge_cents !== null && sig.edge_cents !== undefined
    ? ` · Edge <b>${sig.edge_cents > 0 ? "+" : ""}${sig.edge_cents}¢</b>` : "";
  let html = `
    ${badge(sig.recommendation, sig.strength)}
    <div class="probbar">
      <div class="fill" style="width:${pctYes}%"></div>
      <div class="lbl">YES ${sig.fair_yes_cents}¢ &nbsp;|&nbsp; NO ${sig.fair_no_cents}¢</div>
    </div>
    <div class="note rationale">${sig.rationale}</div>
    <div class="kv">
      <span>Spot <b>$${sig.spot.toLocaleString()}</b></span>
      <span>Closes in <b>${fmtCountdown(secs)}</b></span>
      <span>Momentum <b>${sig.momentum_pct}%</b></span>
      <span>Exp. move <b>±${sig.expected_move_pct}%</b></span>
      <span>Confidence <b>${sig.confidence}%</b></span>${edgeTxt}
    </div>`;
  if (sig.dip_note) html += `<div class="note dip">💡 ${sig.dip_note}</div>`;
  if (sig.exit_hint) html += `<div class="note exit">🎯 ${sig.exit_hint}</div>`;
  return html;
}

// ---- Tracked markets list -------------------------------------------------
function renderMarket(m) {
  const dirTxt = m.direction === "above" ? "above" : "below";
  const title = `${m.coin} ${dirTxt} $${Number(m.threshold).toLocaleString()}`;
  const x = `<button class="x" title="Stop tracking" onclick="delMarket(${m.id})">✕</button>`;

  if (m.resolved) {
    const oc = m.outcome === "YES" ? "yes" : "no";
    let verdict = "";
    if (m.correct === 1) verdict = `<span class="correct">✓ model was right</span>`;
    else if (m.correct === 0) verdict = `<span class="wrong">✗ model missed</span>`;
    else verdict = `<span class="meta">(model said HOLD)</span>`;
    return `<div class="market resolved">
      <div class="top">
        <div>
          <div class="title">${title}</div>
          <div class="meta">Closed ${fmtClock(m.close_time)} · settled $${Number(m.resolve_price).toLocaleString()}</div>
        </div>${x}
      </div>
      <div class="kv">
        <span>Resolved <b class="outcome ${oc}">${m.outcome}</b></span>
        <span>Model called <b>${m.snap_recommendation}</b></span>
        <span>${verdict}</span>
      </div>
    </div>`;
  }

  const sig = m.signal;
  const body = sig
    ? renderSignalBody(sig, m.close_time, m.id)
    : `<div class="note rationale">${m.signal_error || "loading…"}</div>`;

  // Sell guidance if a position is held; otherwise an "I bought this" form.
  let posHtml;
  if (m.position) {
    const p = m.position;
    const cls = p.action === "SELL" ? "sellbox sell" : "sellbox hold";
    const pnl = p.pnl_cents;
    const pnlTxt = pnl == null ? "" :
      `<span class="${pnl >= 0 ? "ev pos" : "ev neg"}">${pnl >= 0 ? "+" : ""}${pnl}¢${p.pnl_pct != null ? ` (${p.pnl_pct >= 0 ? "+" : ""}${p.pnl_pct}%)` : ""}</span>`;
    posHtml = `<div class="${cls}">
      <div class="sellhead"><span class="sellaction">${p.action === "SELL" ? "🔔 " : "⏳ "}${p.headline}</span>${pnlTxt}</div>
      <div class="small">You hold <b>${p.side}</b>, bought at <b>${p.entry_cost_cents}¢</b> · sell now ~<b>${p.sell_price_cents}¢</b>${p.sell_price_estimated ? " (est.)" : ""} · fair value <b>${p.fair_value_cents}¢</b></div>
      <div class="choices">
        <div class="choice"><b>💵 Sell now</b><br>get ~${p.sell_price_cents}¢ ${pnl != null ? `(${pnl >= 0 ? "+" : ""}${pnl}¢)` : ""} locked in</div>
        <div class="choice"><b>⏳ Hold to close</b><br>worth ~${p.fair_value_cents}¢ avg (100¢ or 0¢)</div>
      </div>
      <div class="small">${p.detail}</div>
      ${p.flip ? `<div class="note dip" style="border-color:var(--no);color:var(--no)">🔄 ${p.flip.note}</div>` : ""}
      ${p.settle_note ? `<div class="small" style="margin-top:4px">⏰ ${p.settle_note}</div>` : ""}
      <button class="track-mini" style="margin-top:8px" onclick="clearPosition(${m.id})">clear position</button>
    </div>`;
  } else {
    const f = "pos_" + m.id;
    posHtml = `<div class="addpos">
      <button class="track-mini" onclick="document.getElementById('${f}').classList.toggle('hidden')">＋ I bought this</button>
      <div class="buyform hidden" id="${f}">
        I bought
        <select id="${f}_side"><option value="YES">YES</option><option value="NO">NO</option></select>
        at <input id="${f}_cost" type="number" step="any" min="0" max="100" placeholder="cost" style="width:64px"/> ¢
        <button class="track-mini primary-mini" onclick="savePosition(${m.id})">Save</button>
      </div>
    </div>`;
  }

  return `<div class="market">
    <div class="top">
      <div>
        <div class="title">${title}</div>
        <div class="meta">Closes ${fmtClock(m.close_time)}</div>
      </div>${x}
    </div>
    ${posHtml}
    ${body}
  </div>`;
}

window.savePosition = async (id) => {
  const f = "pos_" + id;
  const side = document.getElementById(f + "_side").value;
  const cost = parseFloat(document.getElementById(f + "_cost").value);
  if (isNaN(cost)) return;
  await fetch(`/api/markets/${id}/position`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ position_side: side, entry_cost_cents: cost }),
  });
  refreshMarkets();
};
window.clearPosition = async (id) => {
  await fetch(`/api/markets/${id}/position`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ position_side: null, entry_cost_cents: null }),
  });
  refreshMarkets();
};

async function refreshMarkets() {
  try {
    const [mRes, sRes] = await Promise.all([
      fetch("/api/markets"),
      fetch("/api/stats"),
    ]);
    const markets = await mRes.json();
    const stats = await sRes.json();

    const box = $("markets");
    if (!markets.length) {
      box.innerHTML = `<div class="empty">No markets yet. Add one above to get a live signal.</div>`;
    } else {
      // Active first, then resolved.
      markets.sort((a, b) => (a.resolved - b.resolved) || (a.close_time - b.close_time));
      box.innerHTML = markets.map(renderMarket).join("");
    }

    if (stats.scored_markets) {
      $("statsBar").innerHTML =
        `Accuracy <b>${stats.accuracy_pct}%</b> (${stats.wins}/${stats.scored_markets}) · ` +
        `Brier <b>${stats.brier_score}</b>`;
    } else {
      $("statsBar").innerHTML = `<span>No resolved markets yet</span>`;
    }
  } catch (e) {
    /* transient network hiccup; next tick retries */
  }
}

window.delMarket = async (id) => {
  await fetch("/api/markets/" + id, { method: "DELETE" });
  refreshMarkets();
};

// ---- Kalshi live scanner --------------------------------------------------
function strikeToTracker(m) {
  // Map a Kalshi market's geometry onto our (direction, threshold) tracker.
  const st = (m.strike_type || "").toLowerCase();
  if (st === "greater" || st === "greater_or_equal") return { direction: "above", threshold: m.floor };
  if (st === "less" || st === "less_or_equal") return { direction: "below", threshold: m.cap };
  return null; // 'between' isn't representable in the simple tracker
}

let lastScan = { coin: null, timeframe: null };
const scanCtx = {}; // ticker -> context for the "I bought this" form

function renderVol(v) {
  if (!v) return "";
  const hasImplied = v.ratio != null;
  const cls = !hasImplied ? "" : v.ratio >= 1.15 ? "ev neg" : v.ratio <= 0.87 ? "ev pos" : "";
  const moveLine = hasImplied
    ? `implied <b>${v.implied_move_pct}%</b> vs realized <b>${v.realized_move_pct}%</b> move · ratio <b class="${cls}">${v.ratio}×</b>`
    : `realized move <b>${v.realized_move_pct}%</b>`;
  let deribit = "";
  if (v.deribit_dvol_pct) {
    const dcls = v.deribit_ratio >= 1.15 ? "ev neg" : v.deribit_ratio <= 0.87 ? "ev pos" : "";
    deribit = `<div class="small" style="margin-top:6px">🛰️ vs Deribit (sharp options): DVOL <b>${v.deribit_dvol_pct}%</b>${v.deribit_ratio != null ? ` (<b class="${dcls}">${v.deribit_ratio}×</b>)` : ""} — ${v.deribit_note}</div>`;
  }
  return `<div class="volbox">
    <div class="sellhead"><span class="sellaction">📊 ${v.verdict}</span>
      <span class="small">${moveLine}</span></div>
    <div class="small">${v.suggestion}</div>
    ${deribit}
  </div>`;
}

function recSide(sig) {
  if (sig.recommendation === "BUY YES") return "YES";
  if (sig.recommendation === "BUY NO") return "NO";
  return null;
}

function renderScanRow(m) {
  const sig = m.signal;
  const secs = m.close_time ? m.close_time - Math.floor(Date.now() / 1000) : 0;
  const bestEdge = m.best_edge;
  const side = recSide(sig);
  const rowCls = sig.recommendation !== "HOLD" ? "scanrow edge" : "scanrow";
  const tracker = strikeToTracker(m);
  const fid = "buy_" + (m.ticker || "").replace(/[^a-z0-9]/gi, "_");

  // One clear action line: what to buy, at what price, vs fair value.
  let action;
  if (side) {
    const cost = side === "YES" ? m.yes_ask : m.no_ask;
    const fair = side === "YES" ? sig.fair_yes_cents : sig.fair_no_cents;
    const stake = stakeText(fair / 100, cost);
    action = `<div class="actionline">
        ${badge(sig.recommendation, sig.strength)}
        <span class="edgeval pos">+${bestEdge}¢ edge</span>
      </div>
      <div class="plain">✅ Buy <b>${side}</b> at <b>${cost}¢</b> → fair <b>${fair}¢</b>${sig.confidence != null ? ` · <b>${sig.confidence}%</b> confidence` : ""}${sig.dip_note ? ` · 💡 ${sig.dip_note}` : ""}</div>
      ${stake ? `<div class="small" style="margin-top:4px">${stake}</div>` : ""}`;
  } else {
    action = `<div class="actionline">${badge("HOLD", "flat")}
        <span class="edgeval neg">no clear edge</span></div>
      <div class="plain">Model fair YES <b>${sig.fair_yes_cents}¢</b> vs market — wait for a better price.</div>`;
  }

  scanCtx[m.ticker] = {
    coin: lastScan.coin, threshold: tracker ? tracker.threshold : null,
    direction: tracker ? tracker.direction : null, close_time: m.close_time,
    ticker: m.ticker, yes_ask: m.yes_ask, no_ask: m.no_ask, side,
  };

  const buttons = tracker ? `
    <div class="scanbtns">
      <button class="track-mini primary-mini" onclick="showBuyForm('${m.ticker}')">I bought this</button>
      <button class="track-mini" onclick='trackFromScan(${JSON.stringify({
        coin: lastScan.coin, threshold: tracker.threshold, direction: tracker.direction,
        close_time: m.close_time, kalshi_ticker: m.ticker, yes_price_cents: m.yes_ask,
      })})'>Just watch</button>
    </div>
    <div class="buyform hidden" id="${fid}">
      I bought
      <select id="${fid}_side">
        <option value="YES"${side === "YES" ? " selected" : ""}>YES</option>
        <option value="NO"${side === "NO" ? " selected" : ""}>NO</option>
      </select>
      at <input id="${fid}_cost" type="number" step="any" min="0" max="100"
                placeholder="${side === "NO" ? m.no_ask : m.yes_ask}" style="width:64px"/> ¢
      <button class="track-mini primary-mini" onclick="saveBuy('${m.ticker}')">Save</button>
      <button class="track-mini" onclick="hideBuyForm('${m.ticker}')">cancel</button>
    </div>` : "";

  const ns = sig.near_settlement;
  const nsHtml = ns
    ? `<div class="note dip" style="border-color:var(--yes);color:var(--yes)">⏱ Near settlement (${ns.mins}m left): outcome looks ${ns.side === "YES" ? "YES" : "NO"} (model ${ns.fair}¢) but you can still buy <b>${ns.side}</b> at <b>${ns.ask}¢</b> — convergence edge.</div>`
    : "";
  return `<div class="${rowCls}">
    <div class="scanhead">
      <div class="strike">${m.subtitle || m.ticker}</div>
      <div class="small">closes ${fmtCountdown(secs)} · Kalshi YES ${m.yes_ask ?? "–"}¢ / NO ${m.no_ask ?? "–"}¢</div>
    </div>
    ${action}
    ${nsHtml}
    ${buttons}
  </div>`;
}

function fid(ticker) { return "buy_" + ticker.replace(/[^a-z0-9]/gi, "_"); }
window.showBuyForm = (t) => { const el = document.getElementById(fid(t)); if (el) el.classList.remove("hidden"); };
window.hideBuyForm = (t) => { const el = document.getElementById(fid(t)); if (el) el.classList.add("hidden"); };

window.saveBuy = async (ticker) => {
  const ctx = scanCtx[ticker];
  if (!ctx || !ctx.direction) return;
  const f = fid(ticker);
  const sideEl = document.getElementById(f + "_side");
  const costEl = document.getElementById(f + "_cost");
  const sideVal = sideEl.value;
  let cost = parseFloat(costEl.value);
  if (isNaN(cost)) cost = sideVal === "YES" ? ctx.yes_ask : ctx.no_ask; // default to the ask
  await fetch("/api/markets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      coin: ctx.coin, threshold: ctx.threshold, direction: ctx.direction,
      close_time: ctx.close_time, kalshi_ticker: ticker,
      position_side: sideVal, entry_cost_cents: cost,
    }),
  });
  hideBuyForm(ticker);
  refreshMarkets();
};

window.trackFromScan = async (body) => {
  await fetch("/api/markets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  refreshMarkets();
};

async function runScan() {
  const coin = $("scanCoin").value;
  const timeframe = $("scanTimeframe").value;
  lastScan = { coin, timeframe };
  const box = $("scanResults");
  box.innerHTML = `<div class="empty">Scanning ${coin} ${timeframe}…</div>`;
  try {
    const r = await fetch(`/api/kalshi/scan?coin=${coin}&timeframe=${timeframe}`);
    const d = await r.json();
    if (d.error) { box.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    if (!d.markets || !d.markets.length) {
      box.innerHTML = `<div class="empty">No open ${coin} ${timeframe} contracts on Kalshi right now.</div>`;
      return;
    }
    box.innerHTML = renderVol(d.vol) + d.markets.map(renderScanRow).join("");
  } catch (e) {
    box.innerHTML = `<div class="empty">Scan failed — retrying on next refresh.</div>`;
  }
}

// ---- Create market --------------------------------------------------------
async function trackMarket() {
  const threshold = parseFloat($("threshold").value);
  const close = computeCloseTime();
  const msg = $("setupMsg");
  if (!threshold) { msg.textContent = "Enter a threshold."; return; }
  if (!close) { msg.textContent = "Pick a valid close time."; return; }
  if (close <= Math.floor(Date.now() / 1000)) { msg.textContent = "Close time must be in the future."; return; }

  const body = {
    coin: $("coin").value,
    threshold,
    direction: $("direction").value,
    close_time: close,
  };
  const yp = $("yesPrice").value;
  if (yp) body.yes_price_cents = parseFloat(yp);

  msg.textContent = "Adding…";
  const r = await fetch("/api/markets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok) { msg.textContent = data.error || "Error"; return; }
  msg.textContent = "Tracking ✓";
  setTimeout(() => (msg.textContent = ""), 1500);
  refreshMarkets();
}

// ---- Baseball insights ----------------------------------------------------
function liveHeader(g) {
  const lv = g.live || {};
  const aR = lv.away_runs, hR = lv.home_runs;
  if (lv.is_live) {
    const half = lv.inning_state ? `${lv.inning_state} ${lv.inning}` : "";
    return `<div class="livebox live">🔴 LIVE · ${half}<span class="score">${g.away_abbr} ${aR ?? 0} – ${hR ?? 0} ${g.home_abbr}</span></div>`;
  }
  if (lv.is_final) {
    return `<div class="livebox final">FINAL<span class="score">${g.away_abbr} ${aR ?? 0} – ${hR ?? 0} ${g.home_abbr}</span></div>`;
  }
  return `<div class="livebox sched">${g.status || "Scheduled"}</div>`;
}

function renderProps(g) {
  const p = g.props;
  if (!p) return "";
  const rl = p.run_line;
  const totals = p.totals.map((t) =>
    `${t.line} <b>O ${t.over_pct}%</b> / U ${t.under_pct}%`).join(" &nbsp;·&nbsp; ");
  const hitCols = (side, label) => {
    const h = side;
    if (!h) return `<div><div class="teamhdr">${label}</div><div class="small">lineup not posted yet</div></div>`;
    const tt = h.team_total_hits;
    const rows = h.batters.map((b) =>
      `<div class="hitrow"><span>${b.name}</span><span>1+ <b>${b.hit1_pct}%</b> · 2+ ${b.hit2_pct}%</span></div>`).join("");
    return `<div>
      <div class="teamhdr">${label}</div>
      <div class="small">Team hits o/u <b>${tt.line}</b>: over <b>${tt.over_pct}%</b></div>
      ${rows}
    </div>`;
  };
  return `<details class="props">
    <summary>📊 Props &amp; odds — run line, totals, hit props</summary>
    <div class="propgrid">
      <div class="propcard">
        <div class="teamhdr">Run line (margin)</div>
        <div class="small"><b>${rl.favorite} −1.5</b> (win by 2+): <b>${rl.fav_by2_pct}%</b></div>
        <div class="small">${rl.underdog} +1.5 (stays within 1): <b>${rl.dog_plus15_pct}%</b></div>
        <div class="teamhdr" style="margin-top:8px">Total runs (model ${p.model_total})</div>
        <div class="small">${totals}</div>
      </div>
      <div class="propcard">
        <div class="teamhdr">Hit props (1+ / 2+ hits)</div>
        <div class="hitgrid">
          ${hitCols(p.hits_away, g.away_abbr)}
          ${hitCols(p.hits_home, g.home_abbr)}
        </div>
      </div>
    </div>
  </details>`;
}

function spLine(sp) {
  if (!sp || sp.era == null) return `${sp && sp.name ? sp.name : "TBD"} <span style="color:var(--border)">(no stats)</span>`;
  const hand = sp.hand ? `${sp.hand}HP` : "";
  const recent = sp.recent_era != null ? ` · last5 <b>${sp.recent_era}</b> ERA` : "";
  return `${sp.name} (${hand}) — <b>${sp.era}</b> ERA, <b>${sp.whip}</b> WHIP, ${sp.ip} IP${recent}`;
}

function renderGame(g) {
  const pct = Math.round(g.pick_prob * 100);
  const edge = g.edge_cents;
  const cls = edge != null && edge >= 5 ? "bbgame edge" : "bbgame";
  const ht = g.home_team, at = g.away_team;
  let market = g.pick_price_cents != null
    ? `Kalshi ${g.pick_price_cents}¢ · <b class="${edge >= 0 ? "ev pos" : "ev neg"}">${edge >= 0 ? "+" : ""}${edge}¢ edge</b>`
    : `<span style="color:var(--muted)">no Kalshi price matched</span>`;
  if (g.pick_price_cents != null) {
    const st = stakeText(g.pick_prob, g.pick_price_cents);
    if (st) market += ` · ${st}`;
  }
  const rec = (t) => (t.wins != null ? `${t.wins}-${t.losses} (${t.run_diff >= 0 ? "+" : ""}${t.run_diff})` : "");
  // platoon: away offense faces the home starter's hand, and vice-versa
  const plat = (off, oppSp) => off.ops_vs_opp_hand
    ? ` (vs ${oppSp.hand}HP <b>${off.ops_vs_opp_hand}</b>)` : "";
  const lf = (t) => {
    if (t.lineup_factor == null) return "";
    const arrow = t.lineup_factor > 1.02 ? " ▲" : t.lineup_factor < 0.98 ? " ▼" : "";
    return ` · lineup OPS <b>${t.lineup_ops}</b>${arrow}`;
  };
  const w = g.weather;
  let wxLine = "";
  if (w && w.available) {
    wxLine = `<div class="small">🌤️ ${w.stadium}: <b>${w.temp_f}°F</b>, wind ${w.wind_mph}mph ${w.wind_dir} (${w.wind_effect})${w.precip_pct ? `, ${w.precip_pct}% precip` : ""}${w.summary ? ` · ${w.summary}` : ""} <span style="color:var(--border)">[${w.source}]</span></div>`;
  } else if (w && w.roof === "fixed") {
    wxLine = `<div class="small">🏟️ ${w.stadium || "Indoor"}: dome — weather neutral</div>`;
  }
  return `<div class="${cls}" data-pk="${g.game_pk}">
    <div class="top">
      <div>
        <div class="matchup">${g.matchup}</div>
        <div class="pick">Pick: ${g.pick} &nbsp;(${g.pick_pct}%) · conf ${g.confidence}%</div>
      </div>
      ${liveHeader(g)}
    </div>
    <div class="winbar"><div class="fill" style="width:${pct}%"></div>
      <div class="lbl">${g.away_name.split(" ").pop()} ${Math.round(g.p_away*100)}% — ${Math.round(g.p_home*100)}% ${g.home_name.split(" ").pop()}</div>
    </div>
    ${g.in_game ? `<div class="small" style="color:var(--no)">📈 Live in-game win probability — ${g.in_game.state} ${g.in_game.inning}, ${g.in_game.outs} out${g.in_game.on_base.length ? `, runners on ${g.in_game.on_base.join("/")}` : ", bases empty"}</div>` : ""}
    <div class="small">Expected runs: <b>${g.exp_runs_away}</b> ${g.away_abbr} — <b>${g.exp_runs_home}</b> ${g.home_abbr} · total <b>${g.exp_total}</b> (park ${g.park_factor})</div>
    ${wxLine}
    <div class="matchgrid">
      <div>
        <div class="teamhdr">${g.away_abbr} ${rec(at)} · away</div>
        <div class="small">SP: ${spLine(g.away_sp)}</div>
        <div class="small">Team OPS <b>${at.ops}</b>${plat(at, g.home_sp)} · ${at.rpg} R/G · bullpen <b>${at.bullpen_era}</b> ERA, ${at.bullpen_whip} WHIP${lf(at)}</div>
      </div>
      <div>
        <div class="teamhdr">${g.home_abbr} ${rec(ht)} · home</div>
        <div class="small">SP: ${spLine(g.home_sp)}</div>
        <div class="small">Team OPS <b>${ht.ops}</b>${plat(ht, g.away_sp)} · ${ht.rpg} R/G · bullpen <b>${ht.bullpen_era}</b> ERA, ${ht.bullpen_whip} WHIP${lf(ht)}</div>
      </div>
    </div>
    <div class="small" style="margin-top:8px">${market}</div>
    ${renderProps(g)}
  </div>`;
}

// Combo maker state + builder.
let bbCombosData = null;
let parlayLegs = 3;
let parlayTarget = 65;
let parlayPayout = 0;
window.buildParlay = async () => {
  const out = $("parlayOut");
  if (!out || !bbCombosData) return;
  let n = parseInt(($("parlayN") || {}).value, 10);
  const maxN = bbCombosData.max_legs_available || 2;
  if (isNaN(n) || n < 2) n = 2;
  if (n > maxN) n = maxN;
  parlayLegs = n;
  let t = parseInt(($("parlayTarget") || {}).value, 10); if (isNaN(t)) t = 65;
  parlayTarget = t;
  let p = parseFloat(($("parlayPayout") || {}).value) || 0; parlayPayout = p;
  const date = $("bbDate").value;
  out.innerHTML = `<div class="small">Tuning lines…</div>`;
  try {
    const d = await (await fetch(`/api/baseball/parlay?date=${date}&legs=${n}&target=${t}&payout=${p}`)).json();
    if (!d.combo) { out.innerHTML = `<div class="small">Couldn't build a parlay at ${t}%.</div>`; return; }
    const c = d.combo;
    const title = p > 1
      ? `🎯 ${c.n_legs}-leg parlay (≥${t}% legs, ${c.payout_reached ? "reached" : "max"} ${c.fair_payout_x}×)`
      : `🎯 ${c.n_legs}-leg parlay tuned to ${t}%+`;
    const note = (p > 1 && !c.payout_reached)
      ? `<div class="small">Couldn't reach ${p}× with the available games — this is the max (${c.fair_payout_x}×).</div>` : "";
    out.innerHTML = renderCombo(c, title, "hl prop") + note;
  } catch (e) {
    out.innerHTML = `<div class="small">Build failed — try again.</div>`;
  }
};

function renderCombo(c, tag, extraCls) {
  const abbr = (mu) => {
    if (!mu) return "";
    if (mu.includes(" @ ")) return mu.split(" @ ").map((t) => t.split(" ").pop()).join("@");
    return mu.length <= 5 ? mu : "";  // short tags (e.g. coin) ok; long titles skip
  };
  const legs = c.legs.map((l) => {
    const typeTag = l.type ? `<span class="legtag">${l.type}</span> ` : "";
    const liveDot = l.live ? `🔴 ` : "";
    const game = l.matchup ? ` <span class="leggame">${abbr(l.matchup)}</span>` : "";
    return `<li>${liveDot}${typeTag}${l.pick}${game} <span style="color:var(--muted)">(${l.prob_pct}%${l.price_cents != null ? `, ${l.price_cents}¢` : ""})</span></li>`;
  }).join("");
  let nums = `<span>Combined chance <b>${c.combined_prob_pct}%</b></span>
              <span>Fair payout <b>${c.fair_payout_x}×</b></span>`;
  if (c.ev_pct != null) {
    nums += `<span>Parlay payout <b>${c.parlay_payout_x}×</b></span>
             <span>EV <b class="${c.ev_pct >= 0 ? "ev pos" : "ev neg"}">${c.ev_pct >= 0 ? "+" : ""}${c.ev_pct}%</b></span>`;
  }
  return `<div class="combo ${extraCls || ""}">
    <div class="chead">
      <span class="ctag">${tag || c.n_legs + "-team parlay"}</span>
      <span class="small">${c.n_legs} legs</span>
    </div>
    <ul class="legs">${legs}</ul>
    <div class="cnums">${nums}</div>
  </div>`;
}

async function loadBaseball(silent) {
  const gamesBox = $("bbGames");
  const combosBox = $("bbCombos");
  const date = $("bbDate").value;
  if (!silent) {
    gamesBox.innerHTML = `<div class="empty">Loading slate…</div>`;
    combosBox.innerHTML = `<div class="empty">Crunching combos…</div>`;
  }
  try {
    const d = await (await fetch("/api/baseball/today?date=" + date)).json();
    if (d.error) { if (!silent) { gamesBox.innerHTML = `<div class="empty">${d.error}</div>`; combosBox.innerHTML = ""; } return; }
    if (!d.games.length) {
      gamesBox.innerHTML = `<div class="empty">No MLB games scheduled for ${date}.</div>`;
      combosBox.innerHTML = `<div class="empty">No games, no combos.</div>`;
      return;
    }
    // Preserve which games have their props panel expanded across refreshes.
    const open = new Set();
    gamesBox.querySelectorAll(".bbgame[data-pk] details.props[open]").forEach((el) =>
      open.add(el.closest(".bbgame").dataset.pk));
    gamesBox.innerHTML = d.games.map(renderGame).join("");
    open.forEach((pk) => {
      const el = gamesBox.querySelector(`.bbgame[data-pk="${pk}"] details.props`);
      if (el) el.open = true;
    });
    loadBaseballRecord();

    const c = d.combos;
    bbCombosData = c;
    let html = "";
    // Combo maker: pick how many legs; it builds the highest-confidence parlay.
    const maxN = c.max_legs_available || 0;
    if (maxN >= 2) {
      const def = Math.min(parlayLegs, maxN);
      html += `<div class="combomaker">
        🎯 <b>Combo maker:</b> each leg ≥
        <input id="parlayTarget" type="number" min="50" max="97" value="${parlayTarget}" style="width:54px"/>% likely;
        <input id="parlayN" type="number" min="2" max="${maxN}" value="${def}" style="width:50px"/> legs
        <b>or</b> reach <input id="parlayPayout" type="number" min="0" step="any" value="${parlayPayout}" style="width:60px"/>× payout
        <button class="track-mini primary-mini" onclick="buildParlay()">Build</button>
        <div class="small" style="margin-top:4px">It auto-tunes each line (1+/2+ hits, runs total, ML vs ±1.5). Set a payout (e.g. 20×) and it adds legs until the parlay reaches it; leave 0 for a fixed leg count.</div>
        <div id="parlayOut"></div>
      </div>`;
    }
    if (c.safest) html += renderCombo(c.safest, "🛡️ Safest combo", "hl");
    if (c.best_value && JSON.stringify(c.best_value.legs) !== JSON.stringify(c.safest && c.safest.legs))
      html += renderCombo(c.best_value, "💰 Best value (+EV)", "hl value");
    if (c.mixed && c.mixed.length)
      html += renderCombo(c.mixed[0], "🎲 Best prop combo", "hl prop");
    if (c.live && c.live.length) {
      html += `<div class="small" style="margin:12px 0 4px"><b>🔴 Live combos</b> — games in progress right now:</div>`;
      html += c.live.map((x) => renderCombo(x)).join("");
    }
    html += `<div class="small" style="margin:14px 0 4px"><b>Game-winner parlays</b> — by combined chance:</div>`;
    html += c.all.map((x) => renderCombo(x)).join("");
    if (c.mixed && c.mixed.length) {
      html += `<div class="small" style="margin:14px 0 4px"><b>🎲 Mixed combos (incl. props)</b> — moneyline, run line, totals &amp; hit props, one leg per game:</div>`;
      html += c.mixed.map((x) => renderCombo(x)).join("");
    }
    combosBox.innerHTML = html;
    buildParlay();
  } catch (e) {
    gamesBox.innerHTML = `<div class="empty">Failed to load slate.</div>`;
    combosBox.innerHTML = "";
  }
}

function setupTabs() {
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const tab = btn.dataset.tab;
      $("tab-crypto").classList.toggle("hidden", tab !== "crypto");
      $("tab-baseball").classList.toggle("hidden", tab !== "baseball");
      $("tab-sports").classList.toggle("hidden", tab !== "sports");
      $("tab-commodities").classList.toggle("hidden", tab !== "commodities");
      $("tab-weather").classList.toggle("hidden", tab !== "weather");
      $("tab-sim").classList.toggle("hidden", tab !== "sim");
      $("tab-combine").classList.toggle("hidden", tab !== "combine");
      $("tab-ledger").classList.toggle("hidden", tab !== "ledger");
      if (tab === "combine") loadCombineCats();
      if (tab === "sim") initSim();
      if (tab === "commodities" && !$("comResults").dataset.loaded) {
        $("comResults").dataset.loaded = "1";
        loadCommodities();
      }
      if (tab === "baseball" && !$("bbGames").dataset.loaded) {
        $("bbGames").dataset.loaded = "1";
        loadBaseball();
      }
      if (tab === "sports" && !$("sportResults").dataset.loaded) {
        $("sportResults").dataset.loaded = "1";
        loadSports();
      }
      if (tab === "weather" && !$("wxResults").dataset.loaded) {
        $("wxResults").dataset.loaded = "1";
        loadWeather();
      }
      if (tab === "ledger") loadLedger();
    });
  });
}

// ---- Backtest -------------------------------------------------------------
function renderBacktest(r) {
  if (r.error) return `<div class="empty">${r.error}</div>`;
  const roi = r.roi_vs_50_pct;
  let verdict, vcls;
  if (roi != null && roi > 2) { verdict = "✅ Beats a coin-flip market"; vcls = "ev pos"; }
  else if (roi != null && roi < -2) { verdict = "❌ Loses to a coin-flip market — don't trust directional calls at this horizon"; vcls = "ev neg"; }
  else { verdict = "➖ Roughly break-even — no real edge here"; vcls = ""; }

  const cal = r.calibration.map((b) => {
    const off = Math.abs(b.predicted - b.actual);
    const c = off <= 6 ? "var(--yes)" : off <= 14 ? "var(--hold)" : "var(--no)";
    return `<div class="calrow">
      <span>${b.range} <span style="color:var(--muted)">(${b.n})</span></span>
      <span>predicted <b>${b.predicted}%</b> → actual <b style="color:${c}">${b.actual}%</b></span>
    </div>`;
  }).join("");

  return `<div class="bbgame">
    <div class="sellhead"><span class="sellaction ${vcls}">${verdict}</span></div>
    <div class="kv" style="margin-top:6px">
      <span>Decisions tested <b>${r.n.toLocaleString()}</b></span>
      <span>Accuracy <b>${r.accuracy_pct}%</b></span>
      <span>Confident-pick accuracy <b>${r.accuracy_confident_pct}%</b> <span style="color:var(--muted)">(${r.confident_n})</span></span>
    </div>
    <div class="kv">
      <span>Brier <b>${r.brier}</b> <span style="color:var(--muted)">(coin-flip ${r.brier_baseline})</span></span>
      <span>Illustrative ROI vs 50¢ <b class="${roi >= 0 ? "ev pos" : "ev neg"}">${roi >= 0 ? "+" : ""}${roi}%</b></span>
      <span>Bet win rate <b>${r.roi_win_pct}%</b> <span style="color:var(--muted)">(${r.roi_bets} bets)</span></span>
    </div>
    <div class="teamhdr" style="margin-top:10px">Calibration — does the predicted % match what happened?</div>
    <div class="calbox">${cal}</div>
    <div class="small" style="margin-top:8px">Brier below 0.25 and ROI above 0 mean the model is adding real signal. If not, treat this coin/horizon as a coin flip.</div>
  </div>`;
}

async function runBacktest() {
  const box = $("btResults");
  const coin = $("btCoin").value, horizon = $("btHorizon").value;
  box.innerHTML = `<div class="empty">Replaying ${coin} history… (a few seconds)</div>`;
  try {
    const r = await (await fetch(`/api/backtest?coin=${coin}&horizon=${horizon}`)).json();
    box.innerHTML = renderBacktest(r);
  } catch (e) {
    box.innerHTML = `<div class="empty">Backtest failed — try again.</div>`;
  }
}

// ---- Live strategy tracker (real recorded Kalshi prices) ------------------
async function loadStrategy() {
  const box = $("stratResults");
  try {
    const [st, bt] = await Promise.all([
      fetch("/api/recorder/status").then((r) => r.json()),
      fetch("/api/recorder/backtest").then((r) => r.json()),
    ]);
    if (bt.error) { box.innerHTML = `<div class="empty">${bt.error}</div>`; return; }
    let html = `<div class="kv">
      <span>Quotes recorded <b>${(st.samples || 0).toLocaleString()}</b></span>
      <span>Contracts seen <b>${st.tickers || 0}</b></span>
      <span>Settled markets <b>${bt.resolved_markets || 0}</b></span>
      ${bt.calibration_brier != null ? `<span>Model Brier <b>${bt.calibration_brier}</b> (coin-flip 0.25)</span>` : ""}
    </div>`;
    if (!bt.resolved_markets) {
      html += `<div class="empty" style="margin-top:10px">No contracts have settled yet. Leave the app running — the recorder samples every 90s and this fills in as markets close.</div>`;
    } else {
      html += `<div class="teamhdr" style="margin-top:12px">Betting the model's edge at real Kalshi prices (net of fees):</div>
        <div class="calbox">
          <div class="calrow" style="color:var(--muted)"><span>Min edge filter</span><span>Bets · Win% · ROI (net) · gross</span></div>`;
      for (const s of bt.sweep) {
        if (!s.bets) { html += `<div class="calrow"><span>≥ ${s.min_edge}¢</span><span style="color:var(--muted)">no bets</span></div>`; continue; }
        const net = s.roi_net_pct;
        const roiCls = net >= 0 ? "ev pos" : "ev neg";
        html += `<div class="calrow"><span>≥ ${s.min_edge}¢ edge</span>
          <span>${s.bets} · ${s.win_pct}% · <b class="${roiCls}">${net >= 0 ? "+" : ""}${net}%</b> · <span style="color:var(--muted)">${s.roi_pct >= 0 ? "+" : ""}${s.roi_pct}% gross</span></span></div>`;
      }
      html += `</div><div class="small" style="margin-top:8px">ROI is <b>net of Kalshi's ~1.7¢/contract trading fee</b>. Positive net ROI that grows with the edge filter = a real, fee-proof edge. The biggest edges (≥15¢) often mean the model is missing info — the 6–10¢ band is the sweet spot. Small samples are noisy; let it accumulate.</div>`;
    }
    box.innerHTML = html;
  } catch (e) {
    box.innerHTML = `<div class="empty">Couldn't load the tracker.</div>`;
  }
}

// ---- Bet ledger -----------------------------------------------------------
function renderBet(b) {
  const x = `<button class="x" title="Delete" onclick="delBet(${b.id})">✕</button>`;
  if (b.status === "open") {
    return `<div class="market">
      <div class="top">
        <div>
          <div class="title">${b.description || "(bet)"} ${b.side ? `· ${b.side}` : ""}</div>
          <div class="meta">${b.kind} · $${b.stake} @ ${b.price_cents != null ? b.price_cents + "¢" : "—"}</div>
        </div>${x}
      </div>
      <div class="scanbtns">
        <button class="track-mini primary-mini" onclick="settleBet(${b.id},'won')">Won</button>
        <button class="track-mini" onclick="settleBet(${b.id},'lost')">Lost</button>
        <button class="track-mini" onclick="settleBet(${b.id},'void')">Void</button>
      </div>
    </div>`;
  }
  const cls = b.status === "won" ? "ev pos" : b.status === "lost" ? "ev neg" : "";
  const pnl = b.pnl != null ? `${b.pnl >= 0 ? "+" : ""}$${b.pnl.toFixed(2)}` : "";
  return `<div class="market resolved">
    <div class="top">
      <div>
        <div class="title">${b.description || "(bet)"} ${b.side ? `· ${b.side}` : ""}</div>
        <div class="meta">${b.kind} · $${b.stake} @ ${b.price_cents != null ? b.price_cents + "¢" : "—"}</div>
      </div>${x}
    </div>
    <div class="kv"><span>Result <b class="${cls}">${b.status.toUpperCase()}</b></span><span>P/L <b class="${cls}">${pnl}</b></span></div>
  </div>`;
}

async function loadLedger() {
  try {
    const d = await (await fetch("/api/bets")).json();
    const s = d.summary;
    $("ledgerSummary").innerHTML = s.settled
      ? `P/L <b class="${s.total_pnl >= 0 ? "ev pos" : "ev neg"}">${s.total_pnl >= 0 ? "+" : ""}$${s.total_pnl.toFixed(2)}</b> · ROI <b>${s.roi_pct}%</b> · ${s.wins}-${s.losses} (${s.win_pct}%)`
      : `<span>No settled bets yet</span>`;
    const open = d.bets.filter((b) => b.status === "open");
    const settled = d.bets.filter((b) => b.status !== "open");
    $("openBets").innerHTML = open.length ? open.map(renderBet).join("") : `<div class="empty">No open bets. Log one above.</div>`;
    $("settledBets").innerHTML = settled.length ? settled.map(renderBet).join("") : `<div class="empty">Nothing settled yet.</div>`;
  } catch (e) { /* retry next time */ }
}

window.settleBet = async (id, status) => {
  await fetch(`/api/bets/${id}/settle`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  loadLedger();
};
window.delBet = async (id) => {
  await fetch(`/api/bets/${id}`, { method: "DELETE" });
  loadLedger();
};
async function addBet() {
  const stake = parseFloat($("betStake").value);
  const msg = $("betMsg");
  if (!stake) { msg.textContent = "Enter a stake."; return; }
  const body = {
    kind: $("betKind").value,
    description: $("betDesc").value,
    side: $("betSide").value,
    stake,
    price_cents: $("betPrice").value || null,
  };
  await fetch("/api/bets", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  $("betDesc").value = ""; $("betSide").value = ""; $("betStake").value = ""; $("betPrice").value = "";
  msg.textContent = "Logged ✓"; setTimeout(() => (msg.textContent = ""), 1200);
  loadLedger();
}

// ---- Sports browser -------------------------------------------------------
function sfid(t) { return "sport_" + (t || "").replace(/[^a-z0-9]/gi, "_"); }
window.showSportLog = (t) => { const e = document.getElementById(sfid(t)); if (e) e.classList.toggle("hidden"); };
window.logSportBet = async (ticker, kind, desc, name, price) => {
  const f = sfid(ticker);
  const stake = parseFloat(document.getElementById(f + "_stake").value);
  if (isNaN(stake)) return;
  await fetch("/api/bets", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind, description: desc, side: name, stake, price_cents: price }),
  });
  document.getElementById(f).classList.add("hidden");
  const btn = document.getElementById(f + "_btn");
  if (btn) btn.textContent = "logged ✓";
};

function renderSportEvent(e, sportKey) {
  const secs = e.close_time ? e.close_time - Math.floor(Date.now() / 1000) : 0;
  const vig = e.overround_pct;
  const vigCls = vig == null ? "" : vig <= 4 ? "ev pos" : vig >= 10 ? "ev neg" : "";
  const outs = e.outcomes.map((o) => {
    const f = sfid(o.ticker);
    return `<div class="sportout">
      <div class="left">
        <span class="oname">${o.name}</span>
        <span class="small">Kalshi <b>${o.yes_ask != null ? o.yes_ask + "¢" : "—"}</b> · no-vig fair <b>${o.fair_pct != null ? o.fair_pct + "%" : "—"}</b></span>
      </div>
      <button class="track-mini" id="${f}_btn" onclick="showSportLog('${o.ticker}')">Log</button>
      <div class="buyform hidden" id="${f}">
        $<input id="${f}_stake" type="number" step="any" min="0" placeholder="stake" style="width:70px"/>
        <button class="track-mini primary-mini" onclick='logSportBet(${JSON.stringify(o.ticker)},${JSON.stringify(sportKey)},${JSON.stringify(e.title)},${JSON.stringify(o.name)},${o.yes_ask})'>Save</button>
      </div>
    </div>`;
  }).join("");
  const arb = e.arbitrage_pct
    ? `<div class="note dip" style="border-color:var(--yes);color:var(--yes)">💸 Arbitrage: outcome prices sum to ${(100 - e.arbitrage_pct).toFixed(1)}¢ — buying every outcome locks in ~${e.arbitrage_pct}¢ guaranteed profit per $1.</div>`
    : "";
  const pick = e.pick
    ? `<div class="note" style="border:1px solid var(--accent);color:var(--accent)">✅ Buy this one: <b>${e.pick.name}</b> @ ${e.pick.yes_ask}¢ · <b>${e.pick.fair_pct}%</b> confidence (market favorite)</div>`
    : "";
  return `<div class="bbgame">
    <div class="top">
      <div class="matchup">${e.title}</div>
      <div class="small" style="text-align:right">closes ${fmtCountdown(secs)}<br>${vig != null ? `vig <b class="${vigCls}">${vig}%</b>` : ""}</div>
    </div>
    ${pick}
    ${arb}
    <div class="sportouts">${outs}</div>
  </div>`;
}

let sportsLoaded = false;
async function loadSports() {
  if (!sportsLoaded) {
    const meta = await (await fetch("/api/sports/meta")).json();
    $("sportSel").innerHTML = Object.entries(meta).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
    sportsLoaded = true;
  }
  const box = $("sportResults");
  const key = $("sportSel").value;
  box.innerHTML = `<div class="empty">Loading ${key}…</div>`;
  try {
    const d = await (await fetch("/api/sports/" + key)).json();
    if (d.error) { box.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    if (!d.events.length) { box.innerHTML = `<div class="empty">No open ${key} markets right now.</div>`; return; }
    box.innerHTML = d.events.map((e) => renderSportEvent(e, key)).join("");
  } catch (e) {
    box.innerHTML = `<div class="empty">Failed to load.</div>`;
  }
}

// ---- Commodities scanner --------------------------------------------------
let comLoaded = false;
function renderComRow(m) {
  const sig = m.signal;
  const side = sig.recommendation === "BUY YES" ? "YES" : sig.recommendation === "BUY NO" ? "NO" : null;
  const cls = sig.recommendation !== "HOLD" ? "scanrow edge" : "scanrow";
  let action;
  if (side) {
    const cost = side === "YES" ? m.yes_ask : m.no_ask;
    const fair = side === "YES" ? sig.fair_yes_cents : sig.fair_no_cents;
    action = `<div class="actionline">${badge(sig.recommendation, sig.strength)}<span class="edgeval pos">+${m.best_edge}¢ edge</span></div>
      <div class="plain">✅ Buy <b>${side}</b> at <b>${cost}¢</b> → fair <b>${fair}¢</b>${sig.confidence != null ? ` · <b>${sig.confidence}%</b> confidence` : ""}</div>`;
  } else {
    action = `<div class="actionline">${badge("HOLD", "flat")}<span class="edgeval neg">no clear edge</span></div>
      <div class="plain">Model fair YES <b>${sig.fair_yes_cents}¢</b> vs market.</div>`;
  }
  return `<div class="${cls}">
    <div class="scanhead">
      <div class="strike">${m.subtitle || m.ticker}</div>
      <div class="small">closes in ${m.days_to_close}d · Kalshi YES ${m.yes_ask ?? "–"}¢ / NO ${m.no_ask ?? "–"}¢</div>
    </div>
    ${action}
  </div>`;
}
async function loadCommodities() {
  if (!comLoaded) {
    const meta = await (await fetch("/api/commodities/meta")).json();
    $("comSel").innerHTML = Object.entries(meta).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
    comLoaded = true;
  }
  const box = $("comResults");
  const key = $("comSel").value;
  box.innerHTML = `<div class="empty">Scanning ${key}…</div>`;
  try {
    const d = await (await fetch("/api/commodities/scan?key=" + key)).json();
    if (d.error) { box.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    const spot = `<div class="volbox"><div class="sellhead"><span class="sellaction">${d.label}</span><span class="small">our spot <b>$${d.spot}</b> — verify this matches Kalshi's level</span></div></div>`;
    if (!d.markets.length) { box.innerHTML = spot + `<div class="empty">No open ${key} contracts right now.</div>`; return; }
    box.innerHTML = spot + d.markets.map(renderComRow).join("");
  } catch (e) {
    box.innerHTML = `<div class="empty">Scan failed.</div>`;
  }
}

// ---- Weather edge ---------------------------------------------------------
let wxLoaded = false;
async function loadWeather() {
  if (!wxLoaded) {
    const meta = await (await fetch("/api/weather/meta")).json();
    $("wxCity").innerHTML = Object.entries(meta).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
    wxLoaded = true;
  }
  const box = $("wxResults");
  const city = $("wxCity").value;
  box.innerHTML = `<div class="empty">Loading ${city}…</div>`;
  try {
    const d = await (await fetch("/api/weather/" + city)).json();
    if (d.error) { box.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    if (!d.events.length) { box.innerHTML = `<div class="empty">No open ${d.city} temperature markets right now.</div>`; return; }
    const cur = d.current;
    const curBox = cur ? `<div class="volbox">
      <div class="sellhead"><span class="sellaction">🌡️ ${d.city} — live now</span>
        <span class="small">${cur.temp_f != null ? `<b>${cur.temp_f}°F</b>` : ""}${cur.high_so_far_f != null ? ` · high so far <b>${cur.high_so_far_f}°</b>` : ""}</span></div>
      <div class="small">dew point <b>${cur.dew_point_f ?? "—"}°</b> · humidity <b>${cur.humidity_pct ?? "—"}%</b> · wind <b>${cur.wind_mph ?? "—"} mph</b> · pressure <b>${cur.pressure_hpa ?? "—"} hPa</b></div>
    </div>` : "";
    box.innerHTML = curBox + d.events.map((ev) => {
      const rows = ev.outcomes.map((o) => {
        const ec = o.edge_cents;
        const cls = ec == null ? "" : ec >= 7 ? "ev pos" : ec <= -7 ? "ev neg" : "";
        return `<div class="sportout">
          <div class="left">
            <span class="oname">${o.name}</span>
            <span class="small">Kalshi <b>${o.yes_ask != null ? o.yes_ask + "¢" : "—"}</b> · model fair <b>${o.fair_pct != null ? o.fair_pct + "%" : "—"}</b>${ec != null ? ` · edge <b class="${cls}">${ec >= 0 ? "+" : ""}${ec}¢</b>` : ""}</span>
          </div>
        </div>`;
      }).join("");
      const m = ev.model || {};
      const adj = (m.mean != null && m.forecast_high != null && Math.abs(m.mean - m.forecast_high) >= 0.5)
        ? ` <span style="color:var(--accent)">(adj ${m.mean}°)</span>` : "";
      const detail = m.mean != null
        ? `<div class="small">Model high <b>${m.mean}°</b> (forecast ${m.forecast_high}°${m.running_delta != null && Math.abs(m.running_delta) >= 0.5 ? `, running ${m.running_delta > 0 ? "+" : ""}${m.running_delta}° vs schedule` : ""}${m.high_so_far != null ? `, high so far ${m.high_so_far}°` : ""}, ±${m.sigma}° uncertainty)</div>`
        : "";
      const pick = ev.pick
        ? `<div class="note" style="border:1px solid var(--accent);color:var(--accent)">✅ Buy this one: <b>${ev.pick.name}</b> @ ${ev.pick.yes_ask}¢ → fair <b>${ev.pick.fair_pct}%</b> · <b>+${ev.pick.edge_cents}¢ edge</b></div>`
        : "";
      return `<div class="bbgame">
        <div class="top">
          <div class="matchup">${d.city} — high temp ${ev.date}</div>
          <div class="small" style="text-align:right">forecast high<br><b style="color:var(--text);font-size:1.1rem">${m.forecast_high != null ? m.forecast_high + "°F" : "n/a"}</b>${adj}</div>
        </div>
        ${detail}
        ${pick}
        <div class="sportouts">${rows}</div>
      </div>`;
    }).join("");
  } catch (e) {
    box.innerHTML = `<div class="empty">Failed to load.</div>`;
  }
}

// ---- Simulator ------------------------------------------------------------
let simInited = false;
async function initSim() {
  if (simInited) return;
  simInited = true;
  await fillSimKey();
  $("simKind").addEventListener("change", fillSimKey);
  $("simBtn").addEventListener("click", runSim);
}
async function fillSimKey() {
  const kind = $("simKind").value;
  if (kind === "commodity") {
    const meta = await (await fetch("/api/commodities/meta")).json();
    $("simKey").innerHTML = Object.entries(meta).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
    $("simHorizon").innerHTML = [["1", "1 day"], ["3", "3 days"], ["7", "1 week"], ["30", "1 month"]]
      .map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
  } else {
    const coins = await (await fetch("/api/coins")).json();
    $("simKey").innerHTML = coins.map((c) => `<option>${c}</option>`).join("");
    $("simHorizon").innerHTML = [["15", "15 min"], ["60", "1 hour"], ["240", "4 hours"], ["1440", "1 day"]]
      .map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
  }
}
function histBars(h) {
  const max = Math.max(...h.counts, 1);
  return `<div class="hist">${h.counts.map((c) =>
    `<div class="hbar" style="height:${Math.round(100 * c / max)}%"></div>`).join("")}</div>
    <div class="histlabels"><span>$${h.lo.toLocaleString()}</span><span>$${h.hi.toLocaleString()}</span></div>`;
}
async function runSim() {
  const box = $("simResults");
  const kind = $("simKind").value, key = $("simKey").value, horizon = $("simHorizon").value;
  const th = $("simThreshold").value, dir = $("simDir").value;
  box.innerHTML = `<div class="empty">Running 20,000 paths…</div>`;
  try {
    let url = `/api/simulate/price?kind=${kind}&key=${key}&horizon=${horizon}`;
    if (th) url += `&threshold=${th}&direction=${dir}`;
    const d = await (await fetch(url)).json();
    if (d.error) { box.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    const thLine = d.prob_threshold != null
      ? `<div class="note" style="border:1px solid var(--accent);color:var(--accent)">🎯 Chance of finishing ${d.direction} $${d.threshold.toLocaleString()}: <b>${d.prob_threshold}%</b></div>` : "";
    box.innerHTML = `<div class="bbgame">
      <div class="kv">
        <span>Spot <b>$${d.spot.toLocaleString()}</b></span>
        <span>Median <b>$${d.median.toLocaleString()}</b> (${d.median_move_pct >= 0 ? "+" : ""}${d.median_move_pct}%)</span>
        <span>Prob up <b>${d.prob_up}%</b></span>
      </div>
      <div class="kv">
        <span>🟢 Best case (95th) <b class="ev pos">$${d.p95.toLocaleString()} (${d.best_case_move_pct >= 0 ? "+" : ""}${d.best_case_move_pct}%)</b></span>
        <span>🔴 Worst case (5th) <b class="ev neg">$${d.p5.toLocaleString()} (${d.worst_case_move_pct}%)</b></span>
      </div>
      <div class="kv"><span>Extreme high $${d.best.toLocaleString()}</span><span>Extreme low $${d.worst.toLocaleString()}</span><span>50% likely between $${d.p25.toLocaleString()}–$${d.p75.toLocaleString()}</span></div>
      ${thLine}
      <div class="teamhdr" style="margin-top:10px">Outcome distribution</div>
      ${histBars(d.hist)}
    </div>`;
  } catch (e) {
    box.innerHTML = `<div class="empty">Simulation failed.</div>`;
  }
}

// ---- Combo session simulator (client-side; changes nothing) ---------------
let _simCombo = null;
function comboSimControl(combo) {
  _simCombo = { prob: combo.combined_prob_pct / 100, payout: combo.parlay_payout_x || combo.fair_payout_x };
  return `<div class="combomaker" style="margin-top:8px">
    🎲 <b>Simulate</b> <input id="csPlays" type="number" min="1" value="100" style="width:54px"/> plays at $<input id="csStake" type="number" min="1" value="10" style="width:54px"/>
    <button class="track-mini primary-mini" onclick="runComboSim()">Go</button>
    <div id="csOut" class="small" style="margin-top:6px"></div>
  </div>`;
}
window.runComboSim = () => {
  if (!_simCombo) return;
  const plays = Math.max(1, parseInt($("csPlays").value, 10) || 100);
  const stake = Math.max(1, parseFloat($("csStake").value) || 10);
  const p = _simCombo.prob, payout = _simCombo.payout || 1;
  const winAmt = stake * (payout - 1), sessions = 3000, res = [];
  for (let s = 0; s < sessions; s++) {
    let net = 0;
    for (let i = 0; i < plays; i++) net += Math.random() < p ? winAmt : -stake;
    res.push(net);
  }
  res.sort((a, b) => a - b);
  const q = (x) => res[Math.min(res.length - 1, Math.floor(x * res.length))];
  const mean = res.reduce((a, b) => a + b, 0) / res.length;
  const up = res.filter((r) => r > 0).length / res.length * 100;
  const f = (v) => (v >= 0 ? "+$" : "-$") + Math.abs(v).toFixed(0);
  $("csOut").innerHTML =
    `Over ${plays} plays ($${(plays * stake).toLocaleString()} risked): avg <b class="${mean >= 0 ? "ev pos" : "ev neg"}">${f(mean)}</b> · ` +
    `chance of profit <b>${up.toFixed(0)}%</b> · typical range ${f(q(0.05))} to ${f(q(0.95))} · best ${f(res[res.length - 1])} / worst ${f(res[0])}`;
};

// ---- Mega combo maker (cross-category) ------------------------------------
let combineCatsLoaded = false;
async function loadCombineCats() {
  if (combineCatsLoaded) return;
  const meta = await (await fetch("/api/combine/meta")).json();
  $("combineCats").innerHTML = Object.entries(meta).map(([k, v]) =>
    `<label><input type="checkbox" value="${k}" ${["mlb", "crypto"].includes(k) ? "checked" : ""}/> ${v}</label>`
  ).join("");
  combineCatsLoaded = true;
}
async function buildCombine() {
  const cats = [...document.querySelectorAll("#combineCats input:checked")].map((i) => i.value);
  const out = $("combineOut");
  if (!cats.length) { out.innerHTML = `<div class="empty">Pick at least one category.</div>`; return; }
  const n = parseInt($("cmbN").value, 10) || 4;
  const t = parseInt($("cmbTarget").value, 10) || 65;
  const p = parseFloat($("cmbPayout").value) || 0;
  const date = ($("bbDate") && $("bbDate").value) || new Date().toISOString().slice(0, 10);
  out.innerHTML = `<div class="empty">Gathering legs across ${cats.length} categories… (a few seconds)</div>`;
  try {
    const d = await (await fetch(`/api/combine?cats=${cats.join(",")}&legs=${n}&target=${t}&payout=${p}&date=${date}`)).json();
    if (d.error) { out.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    let html = "";
    if (d.counts && Object.keys(d.counts).length)
      html += `<div class="small" style="margin-bottom:8px">Legs available: ${Object.entries(d.counts).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>`;
    if (d.combo) {
      const note = d.combo.legs_meeting_target != null
        ? `<div class="small">${d.combo.legs_meeting_target}/${d.combo.n_legs} legs meet the ${t}% target.</div>` : "";
      html += renderCombo(d.combo, `🎰 ${d.combo.n_legs}-leg mega parlay (≥${t}%)`, "hl prop") + note;
      html += comboSimControl(d.combo);
    } else {
      html += `<div class="empty">No legs available for those categories right now.</div>`;
    }
    out.innerHTML = html;
  } catch (e) {
    out.innerHTML = `<div class="empty">Build failed — try again.</div>`;
  }
}

// ---- Baseball model track record ------------------------------------------
async function loadBaseballRecord() {
  try {
    const r = await (await fetch("/api/baseball/record")).json();
    const el = $("bbRecord");
    if (!el) return;
    if (!r.graded) {
      el.innerHTML = r.pending ? `<span>Model track record: ${r.pending} picks awaiting results…</span>` : "";
      return;
    }
    el.innerHTML = `Model record: <b>${r.wins}-${r.losses}</b> (${r.accuracy_pct}%)` +
      (r.roi_pct != null ? ` · ROI <b class="${r.roi_pct >= 0 ? "ev pos" : "ev neg"}">${r.roi_pct >= 0 ? "+" : ""}${r.roi_pct}%</b>` : "") +
      (r.brier != null ? ` · Brier <b>${r.brier}</b>` : "") +
      (r.pending ? ` · ${r.pending} pending` : "");
  } catch (e) { /* ignore */ }
}

// ---- Wire up --------------------------------------------------------------
async function init() {
  const coins = await (await fetch("/api/coins")).json();
  $("coin").innerHTML = coins.map((c) => `<option>${c}</option>`).join("");

  $("window").addEventListener("change", () => {
    $("customWrap").classList.toggle("hidden", $("window").value !== "custom");
    refreshPreview();
  });
  ["coin", "direction", "threshold", "yesPrice", "customTime"].forEach((id) =>
    $(id).addEventListener("input", () => {
      clearTimeout(previewTimer);
      previewTimer = setTimeout(refreshPreview, 350);
    })
  );
  $("trackBtn").addEventListener("click", trackMarket);

  // Scanner setup
  const meta = await (await fetch("/api/kalshi/meta")).json();
  $("scanCoin").innerHTML = meta.coins.map((c) => `<option>${c}</option>`).join("");
  $("scanBtn").addEventListener("click", runScan);
  $("scanCoin").addEventListener("change", runScan);
  $("scanTimeframe").addEventListener("change", runScan);

  // Bankroll / Kelly settings (persisted locally).
  $("bankroll").value = localStorage.getItem("bankroll") || "";
  $("kellyMult").value = localStorage.getItem("kellyMult") || "0.5";
  $("bankroll").addEventListener("input", () => {
    localStorage.setItem("bankroll", $("bankroll").value);
    if (lastScan.coin) runScan();
  });
  $("kellyMult").addEventListener("change", () => {
    localStorage.setItem("kellyMult", $("kellyMult").value);
    if (lastScan.coin) runScan();
  });

  // Backtest setup
  $("btCoin").innerHTML = meta.coins.map((c) => `<option>${c}</option>`).join("");
  $("btBtn").addEventListener("click", runBacktest);

  // Live strategy tracker + ledger
  $("stratBtn").addEventListener("click", loadStrategy);
  $("betAddBtn").addEventListener("click", addBet);
  loadStrategy();
  setInterval(() => { if (!$("tab-crypto").classList.contains("hidden")) loadStrategy(); }, 60000);

  // Baseball setup
  setupTabs();
  $("bbDate").value = new Date().toISOString().slice(0, 10);
  $("bbBtn").addEventListener("click", () => loadBaseball());
  $("bbRefresh").addEventListener("click", () => loadBaseball(true));

  // Sports setup
  $("sportBtn").addEventListener("click", loadSports);
  $("sportSel").addEventListener("change", loadSports);

  // Weather setup
  $("wxBtn").addEventListener("click", loadWeather);
  $("wxCity").addEventListener("change", loadWeather);

  // Commodities
  $("comBtn").addEventListener("click", loadCommodities);
  $("comSel").addEventListener("change", loadCommodities);

  // Mega combo maker
  $("cmbBtn").addEventListener("click", buildCombine);

  refreshMarkets();
  // Auto-refresh ticks skip while the user is typing / has a form open.
  setInterval(() => { if (!uiBusy()) refreshMarkets(); }, 5000);
  setInterval(() => { if (!uiBusy()) refreshPreview(); }, 5000);
  setInterval(() => { if (lastScan.coin && !uiBusy()) runScan(); }, 8000);
  setInterval(() => {
    if (!uiBusy() && !$("tab-baseball").classList.contains("hidden") && $("bbGames").dataset.loaded) {
      loadBaseball(true);
    }
  }, 20000);
}

init();

// Register the service worker (PWA / installable). Only works on a secure
// context (https or localhost); silently skips otherwise.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}
