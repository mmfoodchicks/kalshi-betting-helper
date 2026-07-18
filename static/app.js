"use strict";

const $ = (id) => document.getElementById(id);

// True while the user is typing in a field or has an inline form open, so
// auto-refresh can skip a beat instead of wiping what they're entering.
function uiBusy() {
  const a = document.activeElement;
  if (a && ["INPUT", "SELECT", "TEXTAREA"].includes(a.tagName)) return true;
  if (document.querySelector(".buyform:not(.hidden)")) return true;
  if (comboBuilding) return true;  // don't re-render the maker mid-build
  return false;
}

// ---- Subscription tiers ---------------------------------------------------
let TIERMATRIX = null;
const TIER_RANK = { free: 0, pro: 1, edge: 2, owner: 3 };
// The server is the source of truth (it returns 'owner' until gating is enforced).
function getTier() { return (TIERMATRIX && TIERMATRIX.current) || localStorage.getItem("tier") || "owner"; }
function setTier(t) {
  localStorage.setItem("tier", t);
  document.cookie = "tier=" + t + ";path=/;max-age=31536000;samesite=lax";
}
// The tier the user has SELECTED (lets the owner preview the customer view).
function selectedTier() {
  const s = $("tierSel");
  return (s && s.value) || localStorage.getItem("tier") || "owner";
}
// God mode: only the owner sees the data/diagnostics panels (track records,
// recorder logs, backtests). Everyone else just gets the service — no fluff.
// True only when the server resolves us to owner AND owner is selected, so a
// previewed (or, once enforced, a real) lower tier hides the data.
function isOwner() {
  const resolved = (TIERMATRIX && TIERMATRIX.current) || "owner";
  return resolved === "owner" && selectedTier() === "owner";
}
function tierHasFeature(feature) {
  const min = TIERMATRIX && TIERMATRIX.feature_min ? TIERMATRIX.feature_min[feature] : null;
  if (!min) return true;
  return TIER_RANK[getTier()] >= TIER_RANK[min];
}
function tierMaxSims() {
  const t = TIERMATRIX && TIERMATRIX.tiers[getTier()];
  return t ? t.max_sims : 1000;
}
function tierLabel(t) {
  return (TIERMATRIX && TIERMATRIX.tiers[t] && TIERMATRIX.tiers[t].label) || t;
}
// A small 🔒 badge for a feature the current tier can't use.
function lockTag(feature) {
  if (tierHasFeature(feature)) return "";
  const min = TIERMATRIX.feature_min[feature];
  return `<span class="locktag" style="color:var(--accent);font-size:.78rem" title="Requires ${tierLabel(min)}">🔒 ${tierLabel(min)}</span>`;
}
// Inline upgrade prompt from a 402 response body.
function upgradeNote(d) {
  const t = d.required_tier;
  return `<div class="note" style="border:1px solid var(--accent);color:var(--accent)">🔒 ${d.message || "Upgrade required."}
    <button class="track-mini primary-mini" onclick="window.bumpTier('${t}')">Switch to ${tierLabel(t)}</button></div>`;
}
window.bumpTier = (t) => { setTier(t); const s = $("tierSel"); if (s) s.value = t; location.reload(); };
function simRunsValue() { const s = $("simRuns"); return s ? parseInt(s.value, 10) || 1000 : 1000; }
function gateSimRuns() {
  const sel = $("simRuns"); if (!sel) return;
  const max = tierMaxSims();
  [...sel.options].forEach((o) => {
    const v = parseInt(o.value, 10);
    o.disabled = v > max;
    o.textContent = o.value.replace(/(\d)(?=(\d{3})+$)/g, "$1,") + (v > max ? " 🔒" : "");
  });
  if (parseInt(sel.value, 10) > max) sel.value = String(Math.min(max, 1000));
}
function applyTierUI() {
  const t = getTier();
  const info = TIERMATRIX && TIERMATRIX.tiers[t];
  if ($("tierHint") && info) {
    $("tierHint").textContent = t === "owner"
      ? "👑 God mode — full access (tiers not enforced)"
      : `${info.price} · up to ${info.max_sims.toLocaleString()} sim runs`;
  }
  if ($("dfsLock")) $("dfsLock").innerHTML = lockTag("dfs");
  gateSimRuns();
  // Hide all owner-only data/diagnostics panels for non-owner tiers.
  document.body.classList.toggle("tier-locked", !isOwner());
}

// ---- Kelly bet sizing -----------------------------------------------------
function getBankroll() { return parseFloat(localStorage.getItem("bankroll")) || 0; }
function getKellyMult() { const v = localStorage.getItem("kellyMult"); return v == null ? 0.5 : parseFloat(v); }
function kellyFraction(prob, costCents) {
  if (prob == null || costCents == null || costCents <= 0 || costCents >= 100) return 0;
  // Size against the EFFECTIVE cost: price + Kalshi's taker fee (~7¢·p·(1−p)),
  // so a thin gross edge that the fee eats sizes to zero instead of a real bet.
  const eff = costCents + 7 * (costCents / 100) * (1 - costCents / 100);
  if (eff >= 100) return 0;
  return Math.max(0, (100 * prob - eff) / (100 - eff));
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
    ? ` · Edge <b>${sig.edge_cents > 0 ? "+" : ""}${sig.edge_cents}¢</b>` +
      (sig.net_edge_cents != null
        ? ` <span class="small" style="color:var(--muted)" title="after Kalshi's ~${sig.fee_cents}¢ taker fee">(net ${sig.net_edge_cents > 0 ? "+" : ""}${sig.net_edge_cents}¢)</span>` : "")
    : "";
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

    if (!isOwner()) {
      $("statsBar").innerHTML = "";   // model accuracy stats are owner-only
    } else if (stats.scored_markets) {
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

// Site-wide calibration audit, fetched once and cached. Returns a small note for
// a given model, or "" when it's a no-op / no data.
let _calReport = null;
async function calNote(model, label) {
  try {
    if (_calReport === null) _calReport = await (await fetch("/api/calibration")).json();
    const c = _calReport && _calReport[model];
    if (!c || c.n < 40 || Math.abs(c.t - 1) < 0.03) return "";
    const verb = c.t > 1 ? "reining in overconfidence" : "sharpening (was underconfident)";
    return `<div class="small" style="color:var(--muted);margin:2px 0 8px">🎯 Calibrated ${label}: <b>${(+c.t).toFixed(2)}×</b> — ${verb}, fit on ${c.n} settled markets.</div>`;
  } catch (e) { return ""; }
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
    const net = side === "YES" ? sig.net_edge_yes_cents : sig.net_edge_no_cents;
    const netTag = net != null
      ? ` <span class="small" style="color:var(--muted)" title="after Kalshi's taker fee">net +${net}¢</span>` : "";
    const stake = stakeText(fair / 100, cost);
    action = `<div class="actionline">
        ${badge(sig.recommendation, sig.strength)}
        <span class="edgeval pos">+${bestEdge}¢ edge${netTag}</span>
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
    // "Buys only" (default on): keep just the actionable BUY YES / BUY NO rows,
    // and show how many flat HOLD contracts were hidden.
    const buysOnly = $("scanBuysOnly") ? $("scanBuysOnly").checked : true;
    const isBuy = (m) => m.signal && m.signal.recommendation && m.signal.recommendation !== "HOLD";
    const shown = buysOnly ? d.markets.filter(isBuy) : d.markets;
    const hidden = d.markets.length - shown.length;
    let head = renderVol(d.vol) + await calNote("crypto", "crypto fair value");
    if (buysOnly) {
      head += shown.length
        ? `<div class="small" style="color:var(--muted);margin:2px 0 8px">Showing <b>${shown.length}</b> buy${shown.length > 1 ? "s" : ""}${hidden ? ` · ${hidden} HOLD hidden` : ""}. <a href="#" id="scanShowAll">show all</a></div>`
        : `<div class="empty">No edges right now — all ${d.markets.length} ${coin} ${timeframe} contracts are a HOLD. <a href="#" id="scanShowAll">show them anyway</a></div>`;
    }
    box.innerHTML = head + shown.map(renderScanRow).join("");
    const showAll = $("scanShowAll");
    if (showAll) showAll.addEventListener("click", (e) => {
      e.preventDefault(); if ($("scanBuysOnly")) $("scanBuysOnly").checked = false; runScan();
    });
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
// Format an ISO start time for a tile in MOUNTAIN TIME, regardless of the
// viewer's device zone (e.g. "5:15 PM MT"). "" if there's no usable time.
function fmtStartTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const opt = { hour: "numeric", minute: "2-digit" };
  try {
    return `${d.toLocaleTimeString("en-US", { ...opt, timeZone: "America/Denver" })} MT`;
  } catch (e) {
    return d.toLocaleTimeString([], opt);
  }
}

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
  const t = fmtStartTime(g.start);
  return `<div class="livebox sched">${t ? `🕒 ${t}` : (g.status || "Scheduled")}</div>`;
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
  const liveTag = p.props_live
    ? ` <span class="ev pos" style="font-size:.8em">● LIVE — updates with the score</span>` : "";
  return `<details class="props">
    <summary>📊 Props &amp; odds — run line, totals, hit props${liveTag}</summary>
    <div class="propgrid">
      <div class="propcard">
        <div class="teamhdr">Run line — Kalshi "win by X+" (adjustable)</div>
        ${[[rl.home, rl.home_by], [rl.away, rl.away_by]].map(([tm, by]) => by
          ? `<div class="small"><b>${tm} by</b> ` + Object.entries(by).map(([m, p]) => `${m}+ <b>${p}%</b>`).join(" · ") + `</div>`
          : `<div class="small"><b>${tm} win by 2+</b>: <b>${tm === rl.home ? rl.home_by2_pct : rl.away_by2_pct}%</b></div>`).join("")}
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
  const fip = sp.fip != null ? `, <b>${sp.fip}</b> FIP` : "";
  const wl = sp.exp_ip != null ? ` · <span title="expected innings this start, from his workload history — sizes the K ladder">~${sp.exp_ip} IP tonight</span>` : "";
  const ip = sp.ip != null && isFinite(+sp.ip) ? Math.round(+sp.ip * 10) / 10 : sp.ip;
  return `${sp.name} (${hand}) — <b>${sp.era}</b> ERA${fip}, <b>${sp.whip}</b> WHIP, ${ip} IP${recent}${wl}`;
}

// ---- Live game feedback feed (pitch counts, AB-by-AB, live model odds) -----
const _liveFeedTimers = {};
window.toggleLiveFeed = (pk) => {
  const box = $(`lf-${pk}`);
  if (!box) return;
  if (box.dataset.open === "1") {
    box.dataset.open = ""; box.innerHTML = "";
    clearInterval(_liveFeedTimers[pk]); delete _liveFeedTimers[pk];
    return;
  }
  box.dataset.open = "1";
  box.innerHTML = `<div class="small" style="padding:8px">Loading live feed…</div>`;
  const load = async () => {
    try {
      const d = await (await fetch(`/api/baseball/live/${pk}`)).json();
      if (box.dataset.open !== "1") return;
      box.innerHTML = d.error ? `<div class="small">${d.error}</div>` : renderLiveFeed(d);
    } catch (e) { box.innerHTML = `<div class="small">Live feed unavailable.</div>`; }
  };
  load();
  _liveFeedTimers[pk] = setInterval(load, 20000);   // refresh while open
};

function renderLiveFeed(d) {
  const s = d.state || {};
  const head = `<div class="lf-state"><b>${d.away}</b> ${s.away_runs ?? 0} – ${s.home_runs ?? 0} <b>${d.home}</b>
    · ${s.half || ""} ${s.inning || ""} · ${s.outs ?? 0} out · ${s.status || ""}</div>`;
  const pit = (d.pitchers || []).map((p) => {
    // Our projected outing for the starter: expected innings + pitch budget
    // (walk-aware — a wild arm gets pulled sooner) alongside the K/9 read.
    const proj = (p.starter && (p.model_k9 || p.est_ip))
      ? `<div class="small lf-proj">📊 proj: ${[
          p.model_k9 ? `${p.model_k9} K/9` : null,
          p.est_ip ? `est ${p.est_ip} IP` : null,
          p.est_pitches ? `~${p.est_pitches} pit` : null,
        ].filter(Boolean).join(" · ")}</div>`
      : "";
    return `<div class="lf-pcard">
      <div class="lf-pname">${p.name} <span class="small">${p.team}</span></div>
      <div class="lf-pline"><b>${p.pitches}</b> pitches · ${p.ip} IP · <b>${p.k}</b> K · ${p.bb} BB · ${p.h} H · ${p.er} ER</div>
      <div class="small">season ERA <b>${p.season_era ?? "—"}</b> · WHIP <b>${p.season_whip ?? "—"}</b></div>
      ${proj}
    </div>`;
  }).join("");
  const abBadge = (ev) => {
    const hit = ["Single", "Double", "Triple", "Home Run"].includes(ev);
    const k = ev === "Strikeout";
    return `<span class="lf-ab ${hit ? "hit" : k ? "k" : "out"}" title="${ev}">${ev === "Home Run" ? "HR" : ev === "Strikeout" ? "K" : ev[0]}</span>`;
  };
  const bvpCell = (h) => {
    const s = h.sim_bvp, b = h.bvp;
    if (s) {   // our pitch-by-pitch sim vs the starter — large sample, pure model
      const cls = s.avg >= 0.290 ? "hit" : (s.avg <= 0.215 ? "k" : "");
      const career = b ? ` · real career ${b.h}-${b.ab} (${b.pa} PA)` : "";
      return `<span class="lf-bvp ${cls}" title="Our pitch-by-pitch sim — ${s.pa} simulated matchups vs ${h.vs_pitcher || "the starter"}: AVG ${s.avg} · ${s.k_pct}% K · ${s.hr_pct}% HR · ${s.bb_pct}% BB${career}">${s.avg}<span class="small" style="opacity:.7"> ${s.k_pct}%K</span></span>`;
    }
    if (b) {   // fall back to the real (small-sample) career line while the sim warms
      const avg = b.ab > 0 ? b.h / b.ab : 0;
      const cls = b.ab >= 3 && avg >= 0.35 ? "hit" : (b.ab >= 5 && avg <= 0.12 ? "k" : "");
      const extra = (b.hr ? ` ${b.hr}HR` : "") + (b.so ? ` ${b.so}K` : "");
      return `<span class="lf-bvp ${cls}" title="Real career vs ${h.vs_pitcher || "the starter"}: ${b.h}-for-${b.ab}${extra}, ${b.bb} BB · OPS ${b.ops || "—"} (${b.pa} PA — small sample)">${b.h}-${b.ab}${b.hr ? ` ${b.hr}HR` : ""}</span>`;
    }
    return `<span class="small" style="color:var(--muted)" title="Simulating this matchup pitch-by-pitch… refresh in a moment">…</span>`;
  };
  const rows = (d.hitters || []).map((h) => `<tr>
    <td>${h.order}. ${h.name}</td>
    <td><b>${h.hits}</b>/${h.ab}</td>
    <td>${bvpCell(h)}</td>
    <td class="lf-log">${(h.ab_log || []).map(abBadge).join("")}</td>
    <td>${h.model_hit_pct != null ? h.model_hit_pct + "%" : "—"}</td>
    <td>${h.second_given_first != null ? h.second_given_first + "%" : "—"}</td>
    <td>${h.live_next_hit_pct != null ? h.live_next_hit_pct + "%" : "—"}</td>
  </tr>`).join("");
  return `<div class="lf-wrap">
    ${head}
    <div class="lf-pgrid">${pit}</div>
    <table class="lf-table"><thead><tr>
      <th>Hitter</th><th>H/AB</th>
      <th title="Our pitch-by-pitch sim's read on this hitter vs today's starter — AVG / K% over hundreds of simulated matchups (real career line in the tooltip)">vs SP (sim)</th>
      <th>AB results</th>
      <th title="Model: chance of 1+ hit this game">Hit%</th>
      <th title="Given a hit, chance of a 2nd">2nd</th>
      <th title="Model chance of a hit in his remaining ABs">Next AB</th>
    </tr></thead><tbody>${rows}</tbody></table>
  </div>`;
}

// Scratch / confirmation guard badge: red when a listed starter has been
// scratched (don't bet the stale read), yellow when the read is provisional
// (starter TBD or lineups not posted), green once the lineups are confirmed.
function confirmBadge(g) {
  const c = g.confirm;
  if (!c || c.level === "final") return "";
  if (c.level === "scratch") return `<div class="cfbadge cf-scratch">🔴 ${c.note}</div>`;
  if (c.level === "provisional") return `<div class="cfbadge cf-prov">🟡 ${c.note}</div>`;
  if (c.home_lineup === "confirmed" && c.away_lineup === "confirmed")
    return `<div class="cfbadge cf-ok">🟢 Lineups confirmed · starters set</div>`;
  return "";
}

function renderGame(g) {
  const pct = Math.round(g.pick_prob * 100);
  const edge = g.edge_cents;
  const cls = edge != null && edge >= 5 ? "bbgame edge" : "bbgame";
  const ht = g.home_team, at = g.away_team;
  const netTxt = g.net_edge_cents != null
    ? ` <span class="small" style="color:var(--muted)" title="Kalshi taker fee ~${g.fee_cents}¢">(net ${g.net_edge_cents >= 0 ? "+" : ""}${g.net_edge_cents}¢ after fee)</span>` : "";
  let market = g.pick_price_cents != null
    ? `Kalshi ${g.pick_price_cents}¢ · <b class="${edge >= 0 ? "ev pos" : "ev neg"}">${edge >= 0 ? "+" : ""}${edge}¢ edge</b>${netTxt}`
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
  // Bullpen fatigue: gassed arms from the last 1-2 days -> weaker pen tonight (leans OVER)
  const bpf = (t) => {
    const f = t.bullpen_fatigue;
    if (!f || !f.count) return "";
    const arms = (f.arms || []).join(", ");
    const pct = Math.round((f.factor - 1) * 100);
    return `<div class="small" style="color:var(--no)" title="${arms}">🔋 Bullpen fatigue: <b>${f.count}</b> arm${f.count > 1 ? "s" : ""} gassed (+${pct}% pen RA9 → leans OVER)</div>`;
  };
  const w = g.weather;
  let wxLine = "";
  if (w && w.roof === "fixed") {
    // Fixed dome: weather has zero effect — say so instead of a misleading wx line.
    wxLine = `<div class="small">🏟️ ${w.stadium || "Indoor"}: <b>dome</b> — weather neutral (no wind/temp effect on runs)</div>`;
  } else if (w && w.available) {
    const rp = w.run_pct;
    const runTag = (rp != null && Math.abs(rp) >= 0.3)
      ? ` → <b class="${rp > 0 ? "ev pos" : "ev neg"}">${rp > 0 ? "+" : ""}${rp}% runs</b>` : "";
    const roofTag = w.roof === "retractable" ? ` · <b>retractable roof</b> (weather at half weight)` : "";
    wxLine = `<div class="small">🌤️ ${w.stadium}: <b>${w.temp_f}°F</b>, wind ${w.wind_mph}mph ${w.wind_dir} — ${w.wind_effect}${runTag}${w.precip_pct ? ` · ${w.precip_pct}% precip` : ""}${w.summary ? ` · ${w.summary}` : ""}${roofTag} <span style="color:var(--border)">[${w.source}]</span></div>`;
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
    ${g.p_home_deep != null ? `<div class="small" style="color:var(--muted)" title="The win% above blends two models: the factor model (team OPS/pitching/platoon/defense/bullpen-fatigue Pythagorean) and the deep player engine (per-player Statcast rates, arsenal matchups, platoon splits, TTO, real bullpen chains, pinch hitters, steals) at 65/35.">🧬 factor model ${Math.round(g.p_home_model*100)}% · deep player engine ${Math.round(g.p_home_deep*100)}% home</div>` : ""}
    ${confirmBadge(g)}
    ${g.in_game ? `<div class="small" style="color:var(--no)">📈 Live in-game win probability — ${g.in_game.state} ${g.in_game.inning}, ${g.in_game.outs} out${g.in_game.on_base.length ? `, runners on ${g.in_game.on_base.join("/")}` : ", bases empty"}</div>
    <button class="track-mini" style="margin-top:6px" onclick="toggleLiveFeed(${g.game_pk})">📡 Live feed — pitches · AB results · model odds</button>
    <div id="lf-${g.game_pk}" class="livefeed"></div>` : ""}
    <div class="small">Expected runs: <b>${g.exp_runs_away}</b> ${g.away_abbr} — <b>${g.exp_runs_home}</b> ${g.home_abbr} · total <b>${g.exp_total}</b> (park ${g.park_factor})</div>
    ${wxLine}
    <div class="matchgrid">
      <div>
        <div class="teamhdr">${g.away_abbr} ${rec(at)} · away</div>
        <div class="small">SP: ${spLine(g.away_sp)}</div>
        <div class="small">Team OPS <b>${at.ops}</b>${plat(at, g.home_sp)} · ${at.rpg} R/G · bullpen <b>${at.bullpen_era}</b> ERA, ${at.bullpen_whip} WHIP${lf(at)}</div>
        ${bpf(at)}
      </div>
      <div>
        <div class="teamhdr">${g.home_abbr} ${rec(ht)} · home</div>
        <div class="small">SP: ${spLine(g.home_sp)}</div>
        <div class="small">Team OPS <b>${ht.ops}</b>${plat(ht, g.away_sp)} · ${ht.rpg} R/G · bullpen <b>${ht.bullpen_era}</b> ERA, ${ht.bullpen_whip} WHIP${lf(ht)}</div>
        ${bpf(ht)}
      </div>
    </div>
    <div class="small" style="margin-top:8px">${market}</div>
    ${renderProps(g)}
  </div>`;
}

// Combo maker state + builder.
let bbCombosData = null;
let bbSlateGames = [];
let bbSlateSort = "confidence";

// Sort the slate for display. Live/soon games always float when sorting by start.
function sortSlateGames(games) {
  const g = games.slice();
  const isLive = (x) => (x.live && (x.live.is_live || x.live.state === "Live")) ? 0 : 1;
  const cmp = {
    confidence: (a, b) => (b.pick_prob || 0) - (a.pick_prob || 0),
    total:      (a, b) => (b.exp_total || 0) - (a.exp_total || 0),
    edge:       (a, b) => (b.edge_cents ?? -999) - (a.edge_cents ?? -999),
    start:      (a, b) => isLive(a) - isLive(b)
                          || (Date.parse(a.start || 0) || 0) - (Date.parse(b.start || 0) || 0),
  }[bbSlateSort] || null;
  return cmp ? g.sort(cmp) : g;
}

// Re-render just the game cards in the current sort order (preserves open props).
function renderSlateGames() {
  const box = $("bbGames");
  if (!box || !bbSlateGames.length) return;
  const open = new Set();
  box.querySelectorAll(".bbgame[data-pk] details.props[open]").forEach((el) =>
    open.add(el.closest(".bbgame").dataset.pk));
  box.innerHTML = sortSlateGames(bbSlateGames).map(renderGame).join("");
  open.forEach((pk) => {
    const el = box.querySelector(`.bbgame[data-pk="${pk}"] details.props`);
    if (el) el.open = true;
  });
}

window.setSlateSort = (v) => { bbSlateSort = v; renderSlateGames(); };
let parlayLegs = 3;
let parlayTarget = 65;
let parlayPayout = 0;
// Combo-maker controls persist across the 20s auto-refresh (the refresh re-renders
// the maker, so without this the selects snap back to defaults — which used to
// revert AND→OR and detach the in-flight result. See comboBuilding guard below.)
let comboLegsModePref = "prefer";
let comboPayoutModePref = "off";
let comboConnPref = "or";
let comboSameGamePref = false;
let comboIncludeLive = false;
let comboGameSel = null;   // null/empty = ALL games; else {pk: true|teamName} selection
let comboBuilding = false; // true while a build is in flight -> pauses auto-refresh

// Prop-type filter for the combo/edge builders. Empty set = all types allowed.
let _mlbTypes = new Set();
const _MLB_TYPES = [["ML", "Moneyline"], ["Total", "Totals"], ["Run line", "Run line"],
  ["Hit", "Hits"], ["HR", "Home runs"], ["Bases", "Total bases"], ["Ks", "Strikeouts"],
  ["RFI", "1st-inn run"], ["HRR", "H+R+RBI"]];
function mlbTypeChipsHTML() {
  return `<span class="ptchips">` + _MLB_TYPES.map(([v, l]) =>
    `<span class="ptchip${_mlbTypes.has(v) ? " on" : ""}" onclick="toggleMlbType(this,'${v}')">${l}</span>`).join("") + `</span>`;
}
function mlbTypeChipRow() {
  return `<div class="small" style="margin-top:8px">Prop types <span style="color:var(--muted)">(none selected = all)</span>: ${mlbTypeChipsHTML()}</div>`;
}
window.toggleMlbType = (el, t) => {
  if (_mlbTypes.has(t)) { _mlbTypes.delete(t); el.classList.remove("on"); }
  else { _mlbTypes.add(t); el.classList.add("on"); }
};
function mlbTypesParam() {
  return _mlbTypes.size ? "&types=" + [..._mlbTypes].map(encodeURIComponent).join(",") : "";
}

// Unified combo maker: one box, routes to the same-game-aware (mixed) builder
// when the checkbox is on, else the one-leg-per-game parlay builder.
window.buildCombo = async () => {
  const out = $("comboOut");
  if (!out) return;
  let n = parseInt(($("comboN") || {}).value, 10); if (isNaN(n) || n < 2) n = 2;
  let t = parseInt(($("comboTarget") || {}).value, 10); if (isNaN(t)) t = 65;
  let p = parseFloat(($("comboPayout") || {}).value) || 0;
  parlayLegs = n; parlayTarget = t; parlayPayout = p;
  // Persist the control choices so the auto-refresh re-renders them as-set.
  comboSameGamePref = !!($("comboSameGame") && $("comboSameGame").checked);
  comboLegsModePref = ($("comboLegsMode") || {}).value || "prefer";
  comboPayoutModePref = ($("comboPayoutMode") || {}).value || "off";
  comboConnPref = ($("comboConn") || {}).value || "or";
  const date = $("bbDate").value;
  // Both modes run through the simulator now, so every leg shows model vs sim.
  // same_game on may stack correlated legs from one game; off = one leg per game.
  comboBuilding = true;
  simLoader(out, comboSameGamePref ? "Simulating games (correlated same-game odds)…" : "Simulating every game…");
  try {
    let q = `legs=${n}&target=${t}&payout=${p}&same_game=${comboSameGamePref ? 1 : 0}`
      + `&legs_mode=${comboLegsModePref}&payout_mode=${comboPayoutModePref}&conn=${comboConnPref}`
      + `&include_live=${comboIncludeLive ? 1 : 0}`;
    const selParam = comboSelParam();
    if (selParam) q += `&sel=${encodeURIComponent(selParam)}`;
    q += mlbTypesParam();
    const d = await (await fetch(`/api/baseball/mixed?date=${date}&${q}`)).json();
    if (d.error === "upgrade_required") { out.innerHTML = upgradeNote(d); return; }
    if (d.error) { out.innerHTML = `<div class="small">${d.error}</div>`; return; }
    if (!d.parlay) { out.innerHTML = `<div class="small">Couldn't build — no eligible games for that selection. Try ALL GAMES, allow live, or loosen a target.</div>`; return; }
    out.innerHTML = renderMixed(d.parlay);
  } catch (e) {
    out.innerHTML = `<div class="small">Build failed — try again.</div>`;
  } finally {
    comboBuilding = false;
  }
};

// Serialize the game grid selection as "pk" (whole game) or "pk:Team" (one team)
// entries joined by commas. Empty -> all games.
function comboSelParam() {
  if (!comboGameSel) return "";
  const parts = [];
  for (const pk in comboGameSel) {
    const v = comboGameSel[pk];
    parts.push(v === true ? String(pk) : `${pk}:${v}`);
  }
  return parts.join(",");
}

// Short label for a full MLB team name ("New York Yankees" -> "Yankees",
// "Boston Red Sox" -> "Red Sox" so the two Sox don't collide).
function teamShort(name) {
  if (!name) return "";
  const t = name.split(" ");
  return ["Sox", "Jays"].includes(t[t.length - 1]) ? t.slice(-2).join(" ") : t[t.length - 1];
}

// The game-selection grid: ALL GAMES + one card per eligible game, each with two
// clickable team chips. Click a team = only that team's legs (auto-selects the
// game); click the card = the whole game; click ALL GAMES = clear to all.
function renderGameGrid(games) {
  const elig = games.filter((g) => ((g.live || {}).state) !== "Final");
  const allOn = !comboGameSel || !Object.keys(comboGameSel).length;
  const esc = (s) => (s || "").replace(/'/g, "\\'");
  let cards = `<div class="gg-card gg-all${allOn ? " on" : ""}" onclick="comboSelectAll()">ALL<br>GAMES</div>`;
  cards += elig.map((g) => {
    const pk = g.game_pk, sel = comboGameSel ? comboGameSel[pk] : undefined;
    const live = (g.live || {}).state === "Live";
    const aOn = sel === true || sel === g.away_name;
    const hOn = sel === true || sel === g.home_name;
    return `<div class="gg-card${sel === true ? " on" : ""}" onclick="comboToggleGame(${pk})" title="${g.matchup || ""}">
      ${live ? '<span class="gg-live">🔴 LIVE</span>' : ""}
      <span class="gg-team${aOn ? " on" : ""}" onclick="event.stopPropagation();comboToggleTeam(${pk},'${esc(g.away_name)}')">${teamShort(g.away_name)}</span>
      <span class="gg-vs">vs</span>
      <span class="gg-team${hOn ? " on" : ""}" onclick="event.stopPropagation();comboToggleTeam(${pk},'${esc(g.home_name)}')">${teamShort(g.home_name)}</span>
    </div>`;
  }).join("");
  return `<div class="gamegrid">${cards}</div>`;
}

function refreshGameGrid() {
  const host = document.querySelector(".combomaker .gamegrid");
  if (host && bbSlateGames.length) host.outerHTML = renderGameGrid(bbSlateGames);
}
window.comboSelectAll = () => { comboGameSel = null; refreshGameGrid(); };
window.comboToggleGame = (pk) => {
  if (!comboGameSel) comboGameSel = {};
  if (comboGameSel[pk] === true) delete comboGameSel[pk]; else comboGameSel[pk] = true;
  refreshGameGrid();
};
window.comboToggleTeam = (pk, team) => {
  if (!comboGameSel) comboGameSel = {};
  if (comboGameSel[pk] === team) delete comboGameSel[pk]; else comboGameSel[pk] = team;
  refreshGameGrid();
};

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
    let title, note = "";
    if (p > 1) {
      title = `🎯 ${c.legs_used}-leg parlay → ${c.fair_payout_x}× (every leg ≥ ${t}%)`;
      if (c.expanded) {
        note += `<div class="small">Added legs up to <b>${c.legs_used}</b> (you asked ${c.requested_legs}) to reach ${p}× while keeping every leg ≥ ${t}%.</div>`;
      }
      if (!c.payout_reached) {
        note += `<div class="small">⚠️ Couldn't reach ${p}× with every leg ≥ ${t}% — the max at that floor is <b>${c.fair_payout_x}×</b> (used ${c.legs_used} legs). Lower the confidence floor or the target to go higher.</div>`;
      }
      note += `<div class="small">At ${c.fair_payout_x}× the chance of hitting is ~<b>${c.combined_prob_pct}%</b> (≈1 in ${Math.round(c.fair_payout_x)}). For fair-odds props that chance is the same however the legs are split — extra legs just land you closer to the exact target.</div>`;
    } else {
      title = `🎯 ${c.n_legs}-leg parlay tuned to ${t}%+`;
    }
    out.innerHTML = renderCombo(c, title, "hl prop") + note;
  } catch (e) {
    out.innerHTML = `<div class="small">Build failed — try again.</div>`;
  }
};

window.buildSGP = async () => {
  const out = $("sgpOut");
  if (!out) return;
  let n = parseInt(($("sgpN") || {}).value, 10); if (isNaN(n) || n < 2) n = 2;
  let t = parseInt(($("sgpTarget") || {}).value, 10); if (isNaN(t)) t = 55;
  let p = parseFloat(($("sgpPayout") || {}).value) || 0;
  const date = $("bbDate").value;
  simLoader(out, "Simulating every game on the slate…");
  try {
    const d = await (await fetch(`/api/baseball/sgp?date=${date}&legs=${n}&target=${t}&payout=${p}`)).json();
    if (d.error === "upgrade_required") { out.innerHTML = upgradeNote(d); return; }
    if (d.error) { out.innerHTML = `<div class="small">${d.error}</div>`; return; }
    if (!d.games || !d.games.length) { out.innerHTML = `<div class="small">No same-game parlays available — need upcoming (non-final) games.</div>`; return; }
    out.innerHTML = `<div class="small" style="margin:6px 0">Best same-game parlay per game (${(d.n_sims || 0).toLocaleString()} sims each):</div>`
      + d.games.map(renderSGP).join("");
  } catch (e) {
    out.innerHTML = `<div class="small">Build failed — try again.</div>`;
  }
};

// Animated progress bar for the long simulations. There's no real per-sim
// progress from the server (it's one request), so the bar eases asymptotically
// toward ~95% and shows a live elapsed timer — enough to prove it's working and
// not frozen. It self-clears once the caller replaces the container's contents.
function simLoader(el, msg) {
  el.innerHTML = `<div class="simloader">
    <div class="small simloader-msg">${msg}</div>
    <div class="simloader-track"><div class="simloader-fill"></div></div>
    <div class="small simloader-meta"><span class="slpct">0%</span> · <span class="sltime">0.0s</span> elapsed — crunching the simulation…</div>
  </div>`;
  const fill = el.querySelector(".simloader-fill");
  const pctEl = el.querySelector(".slpct");
  const timeEl = el.querySelector(".sltime");
  const t0 = performance.now();
  const id = setInterval(() => {
    if (!document.body.contains(fill)) { clearInterval(id); return; }  // results replaced it
    const dt = (performance.now() - t0) / 1000;
    const pct = Math.min(95, 92 * (1 - Math.exp(-dt / 5)));  // asymptotic, never "done" early
    fill.style.width = pct.toFixed(1) + "%";
    pctEl.textContent = pct.toFixed(0) + "%";
    timeEl.textContent = dt.toFixed(1) + "s";
  }, 100);
  return () => clearInterval(id);
}

// Plain-English legend for every number the model shows. Reused wherever model
// numbers appear, so "what does this % mean?" is always one click away.
function modelLegend() {
  return `<details class="modelhelp"><summary>ℹ️ What do these numbers mean?</summary>
    <ul class="legendlist">
      <li><b>Model %</b> — our estimate of the chance this hits, from the matchup math: each hitter's rate vs the opposing pitcher, the starter's K rate, and the run distribution adjusted for park &amp; weather. This is the exact closed-form number.</li>
      <li><b>Sim %</b> — the <i>same</i> outcome measured a different way: we simulate the whole game thousands of times (baserunning, steals, pitch-count &amp; relief, correlations) and count how often it happened. Model and Sim should be close; a gap shows where game context (correlation, a starter getting pulled) moves it.</li>
      <li><b>Market %</b> — Kalshi's price <i>is</i> a probability: a YES at 60¢ means the market thinks ~60%. That's the number we compare against.</li>
      <li><b class="ev pos">Edge</b> (green) / <b class="ev neg">Edge</b> (red) — Model % minus Market %. <b class="ev pos">Green</b> = we think it's underpriced (good value to buy). <b class="ev neg">Red</b> = overpriced (skip). Shown in ¢ because 1% ≈ 1¢ on Kalshi.</li>
      <li><b>Fair payout ×</b> — 1 ÷ our probability (a 25% chance is a fair 4×). The <b>no-vig fair value</b> from our model.</li>
      <li><b>Kalshi pays ×</b> — the <i>real</i> payout from Kalshi's live prices (product of each leg's market price), so it matches what you'd see building the combo on Kalshi. It's lower than our fair payout by their margin. If our fair payout is <i>way</i> above Kalshi's, we strongly disagree with the market on a leg — possible edge, or miscalibration to sanity-check. (Lines post closer to game time, so it may read "—" early.)</li>
      <li><b>Per-leg Kalshi <span class="kmkt">34¢ (2.94×)</span></b> — that leg's live market price and payout, with the <b class="ev pos">+</b>/<b class="ev neg">−</b> edge = our sim % minus Kalshi's price.</li>
      <li><b>Weather → ±% runs</b> — park orientation (home plate → center field) vs the wind: blowing <span class="ev pos">out</span> adds runs, <span class="ev neg">in</span> suppresses them, plus temperature/humidity. This nudges the game total the sim is calibrated to.</li>
    </ul></details>`;
}

// A leg's probability display. Player props carry BOTH the model's closed-form
// % and the simulated %, so we show both (they're computed two different ways);
// other legs (ML/total/run line/HRR) only have the simulated number.
function legProb(l, nsim) {
  const core = (l.model_pct != null)
    ? `model <b>${l.model_pct}%</b> · sim <b>${l.prob_pct}%</b>`
    : `<b>${l.prob_pct}%</b>`;
  const cnt = (nsim && l.sims_hit != null) ? ` · ${l.sims_hit.toLocaleString()}/${nsim.toLocaleString()}` : "";
  // Live Kalshi price for this exact leg, when quoted. Edge = our sim − market.
  let mkt = "";
  if (l.market_cents != null) {
    const edge = Math.round(l.prob_pct - l.market_cents);
    mkt = ` <span class="kmkt">Kalshi <b>${l.market_cents}¢</b>${l.market_payout_x ? ` (${l.market_payout_x}×)` : ""}` +
      ` <span class="${edge >= 0 ? "ev pos" : "ev neg"}">${edge >= 0 ? "+" : ""}${edge}</span></span>`;
  }
  return `<span style="color:var(--muted)">(${core}${cnt})</span>${mkt}`;
}

// Deep per-pitcher / per-hitter simulated detail behind a same-game slip.
function nf(v) { return (v == null ? 0 : v).toLocaleString(); }  // thousands formatter (global)
function renderBreakdown(b, n) {
  if (!b) return "";
  const pit = (b.pitchers || []).map((p) => {
    const kd = p.k_dist || {};
    const dist = [3, 4, 5, 6, 7, 8, 9, 10].filter((L) => kd[L] != null)
      .map((L) => `${L}+ <b>${kd[L]}%</b>`).join(" · ");
    return `<div class="simrow"><div class="simname">⚾ ${p.name} <span class="dfs-team">SP</span></div>
      <div class="small">~<b>${p.exp_k}</b> K · ${p.avg_ip} IP · <b>${p.avg_pitches}</b> pitches before relief · bullpen ~${p.bullpen_exp_k} K</div>
      <div class="small">Strikeouts: ${dist}</div></div>`;
  }).join("");
  const hitSide = (side, label) => {
    const rows = (b.hitters && b.hitters[side] || []).map((h) => {
      const hd = h.hits_dist || {}, td = h.tb_dist || {};
      return `<div class="simrow"><div class="simname">${h.name}</div>
        <div class="small">~${h.exp_hits} H · ${h.exp_tb} TB · ${h.exp_hr} HR${h.exp_sb >= 0.05 ? ` · ${h.exp_sb} SB` : ""}</div>
        <div class="small">1+H <b>${hd["1"]}%</b> · 2+H ${hd["2"]}% · 2+TB ${td["2"]}% · 3+TB ${td["3"]}% · 1+HR <b>${h.p_hr}%</b></div></div>`;
    }).join("");
    return rows ? `<div class="simgroup">${label}</div>${rows}` : "";
  };
  return `<details class="simdetail"><summary>🔬 Simulation detail — every pitcher &amp; hitter (${nf(b.n_sims)} sims)</summary>
    <div class="simwrap">
      <div class="simgroup">Starting pitchers</div>${pit}
      ${hitSide("away", "Away hitters")}${hitSide("home", "Home hitters")}
    </div></details>`;
}

// Real Kalshi combo payout (product of each leg's live market price), shown next
// to our fair payout so the number matches Kalshi's own builder.
function kalshiPayout(m) {
  if (m.kalshi_payout_x == null) {
    return `<span style="color:var(--muted)">Kalshi pays — <span class="small">(no live prices yet — markets post closer to game time)</span></span>`;
  }
  const partial = m.kalshi_full ? "" : ` <span class="small" style="color:var(--muted)">(${m.kalshi_priced}/${m.kalshi_total_legs} legs priced)</span>`;
  const net = m.kalshi_payout_net_x != null
    ? ` <span class="small" style="color:var(--muted)" title="each leg pays Kalshi's ~1–2¢ taker fee">(${m.kalshi_payout_net_x}× net of fees)</span>` : "";
  return `<span>Kalshi pays <b>${m.kalshi_payout_x}×</b>${net}${partial}</span>`;
}

function renderSGP(s) {
  const nsim = s.n_sims || 0;
  const legs = s.legs.map((l) =>
    `<li><span class="legtag">${l.type}</span> ${l.pick} ${legProb(l, nsim)}${simAvgTag(l)}</li>`).join("");
  const corr = s.corr_delta_pct;
  const corrTxt = corr > 0.4 ? `<b style="color:#3ad17a">legs reinforce (+${corr}% vs independent)</b>`
    : corr < -0.4 ? `<b style="color:#e0566a">legs fight each other (${corr}% vs independent)</b>`
    : `<span style="color:var(--muted)">~independent (${corr >= 0 ? "+" : ""}${corr}%)</span>`;
  const cnt = s.combined_sims_hit != null ? ` <span style="color:var(--muted)">(${s.combined_sims_hit.toLocaleString()}/${nsim.toLocaleString()} sims)</span>` : "";
  const warn = s.counteracting
    ? `<div class="small" style="margin:4px 0;padding:5px 8px;border-radius:6px;background:rgba(224,86,106,.12);color:#e0566a"><b>⚠ Counteracting legs</b> — this slate couldn't field a clean parlay, so a pair here works against each other (worst pair corr ${s.worst_pair_corr}). For one leg to hit, another tends to miss. Prefer a different combo.</div>`
    : "";
  return `<div class="combo hl prop">
    <div class="chead">
      <span class="ctag">🎰 ${s.matchup}</span>
      <span class="small">${s.n_legs} legs · ${nsim.toLocaleString()} sims</span>
    </div>
    ${warn}
    <ul class="legs">${legs}</ul>
    <div class="cnums">
      <span>Joint chance <b>${s.combined_prob_pct}%</b>${cnt}</span>
      <span>Fair payout <b>${s.fair_payout_x}×</b></span>
      ${kalshiPayout(s)}
      <span>Correlation: ${corrTxt}</span>
    </div>
    <div class="small" style="margin-top:4px">Naive independent guess: <b>${s.indep_prob_pct}%</b> (${s.indep_payout_x}×). The simulation gives the real correlated number.${s.has_props ? "" : " <i>Run-based legs only — hitter &amp; strikeout props appear once lineups post (a few hours pre-game).</i>"}</div>
    ${renderBreakdown(s.breakdown, nsim)}
  </div>`;
}

window.buildMixed = async () => {
  const out = $("mixOut");
  if (!out) return;
  let n = parseInt(($("mixN") || {}).value, 10); if (isNaN(n) || n < 2) n = 2;
  let t = parseInt(($("mixTarget") || {}).value, 10); if (isNaN(t)) t = 55;
  let p = parseFloat(($("mixPayout") || {}).value) || 0;
  const date = $("bbDate").value;
  simLoader(out, "Simulating the slate (this can take a moment)…");
  try {
    const d = await (await fetch(`/api/baseball/mixed?date=${date}&legs=${n}&target=${t}&payout=${p}`)).json();
    if (d.error === "upgrade_required") { out.innerHTML = upgradeNote(d); return; }
    if (d.error) { out.innerHTML = `<div class="small">${d.error}</div>`; return; }
    if (!d.parlay) { out.innerHTML = `<div class="small">Couldn't build a mixed parlay — need upcoming games.</div>`; return; }
    out.innerHTML = renderMixed(d.parlay);
  } catch (e) {
    out.innerHTML = `<div class="small">Build failed — try again.</div>`;
  }
};

function renderMixed(m) {
  const groups = m.groups.map((g) => {
    const legs = g.legs.map((l) =>
      `<li><span class="legtag">${l.type}</span> ${l.pick} ${legProb(l)}${simAvgTag(l)}</li>`).join("");
    const head = g.same_game
      ? `<div class="small" style="margin:6px 0 2px"><b>🎰 ${g.matchup}</b> — same-game stack, joint <b>${g.joint_pct}%</b></div>`
      : `<div class="small" style="margin:6px 0 2px"><b>${g.matchup}</b> — single leg</div>`;
    return head + `<ul class="legs">${legs}</ul>`;
  }).join("");
  const corr = m.corr_delta_pct;
  const corrTxt = corr > 0.4 ? `<b style="color:#3ad17a">stacking helps (+${corr}% vs independent)</b>`
    : corr < -0.4 ? `<b style="color:#e0566a">stacking costs ${corr}% vs independent</b>`
    : `<span style="color:var(--muted)">~independent</span>`;
  const payNote = m.target_payout_x
    ? `<span>Payout ≥${m.target_payout_x}× <b style="color:${m.payout_reached ? "#3ad17a" : "#e0566a"}">${m.payout_reached ? "✓ reached" : "✗ best is " + m.fair_payout_x + "×"}</b></span>` : "";
  const legNote = (m.legs_target != null)
    ? `<span>${m.legs_target} legs <b style="color:${m.legs_met ? "#3ad17a" : "#e0566a"}">${m.legs_met ? "✓" : "✗ got " + m.n_legs}</b></span>` : "";
  const hardWarn = (m.hard_ok === false)
    ? `<div class="small" style="margin-top:4px;color:#e0566a">⚠️ Couldn't satisfy your required target(s) on today's slate — showing the closest parlay. Try: loosen a target to "recommend", switch AND→OR, lower the payout, or lower the per-leg % (a higher payout needs longer-shot legs).</div>` : "";
  const stacked = m.groups.some((g) => g.same_game);
  return `<div class="combo hl prop">
    <div class="chead">
      <span class="ctag">${stacked ? "🔀 Mixed parlay" : "🎯 Cross-game parlay"}</span>
      <span class="small">${m.n_legs} legs · ${m.n_games} games · ${(m.n_sims || 0).toLocaleString()} sims</span>
    </div>
    ${groups}
    <div class="cnums">
      <span>Combined chance <b>${m.combined_prob_pct}%</b></span>
      <span>Fair payout <b>${m.fair_payout_x}×</b></span>
      ${kalshiPayout(m)}
      ${legNote}
      ${payNote}
      <span>Correlation: ${corrTxt}</span>
    </div>
    ${hardWarn}
    <div class="small" style="margin-top:4px">Naive independent guess: <b>${m.indep_prob_pct}%</b> (${m.indep_payout_x}×). Same-game legs use simulated joint odds; different games multiply. <i>Fair payout is no-vig (1÷our probability) — Kalshi's actual combo pays a bit less (their margin); a much bigger gap means we disagree with the market on a leg.</i></div>
  </div>`;
}

// Small "avg sim 9.1 runs" tag shown under a combo leg when we have a simulated
// average for it (totals, Ks, hits, bases, HR, margins, goals, games, aces…).
function simAvgTag(l) {
  if (!l || l.sim_avg == null || !l.avg_unit) return "";
  const v = (l.avg_unit === "run margin" && l.sim_avg > 0) ? `+${l.sim_avg}` : l.sim_avg;
  return `<span class="legavg">avg sim ${v} ${l.avg_unit}</span>`;
}

function renderCombo(c, tag, extraCls) {
  const abbr = (mu) => {
    if (!mu) return "";
    if (mu.includes(" @ ")) return mu.split(" @ ").map((t) => t.split(" ").pop()).join("@");
    // Tennis / any "A vs B" -> last names, so a leg like "Match in straight
    // sets" isn't a mystery: "Straight sets · Sinner v Alcaraz".
    if (mu.includes(" vs ")) return mu.split(" vs ").map((t) => t.split(" ").pop()).join(" v ");
    return mu.length <= 5 ? mu : "";  // short tags (e.g. coin) ok; long titles skip
  };
  const legs = c.legs.map((l) => {
    const typeTag = l.type ? `<span class="legtag">${l.type}</span> ` : "";
    const liveDot = l.live ? `🔴 ` : "";
    const game = l.matchup ? ` <span class="leggame">${abbr(l.matchup)}</span>` : "";
    // "where on Kalshi" (tennis series + tournament) so the leg is findable.
    const where = l.where ? ` <span class="legwhere" title="find this on Kalshi">📍${l.where}</span>` : "";
    const avg = simAvgTag(l);
    return `<li>${liveDot}${typeTag}${l.pick}${game}${where} <span style="color:var(--muted)">(${l.prob_pct}%${l.price_cents != null ? `, ${l.price_cents}¢` : ""})</span>${avg}</li>`;
  }).join("");
  let nums = `<span>Combined chance <b>${c.combined_prob_pct}%</b></span>
              <span>Fair payout <b>${c.fair_payout_x}×</b></span>`;
  if (c.ev_pct != null) {
    const netEv = c.ev_net_pct != null
      ? ` <span class="small" style="color:var(--muted)" title="after Kalshi's per-leg taker fees">(net ${c.ev_net_pct >= 0 ? "+" : ""}${c.ev_net_pct}%)</span>` : "";
    nums += `<span>Parlay payout <b>${c.parlay_payout_x}×</b></span>
             <span>EV <b class="${c.ev_pct >= 0 ? "ev pos" : "ev neg"}">${c.ev_pct >= 0 ? "+" : ""}${c.ev_pct}%</b>${netEv}</span>`;
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
    // Store the raw slate, then render in the user's chosen sort order (this also
    // preserves which games have their props panel expanded across refreshes).
    bbSlateGames = d.games;
    renderSlateGames();
    loadBaseballRecord();
    loadPropLog();

    const c = d.combos;
    bbCombosData = c;
    let html = "";
    // Unified combo maker: a game-selection grid + the targets, all persisted
    // across the auto-refresh so an in-progress build is never clobbered.
    const maxN = c.max_legs_available || 0;
    // Single-game slates (e.g. the day back from the All-Star break) can't form
    // cross-game combos, so the backend falls back to same-game parlays and flags
    // same_game_only. Show the maker in that case too — defaulted to same-game.
    if (maxN >= 2 || c.same_game_only) {
      const bsKeys = Object.keys(c.by_size || {}).map(Number);
      const effMax = c.same_game_only ? (bsKeys.length ? Math.max(...bsKeys) : 4) : maxN;
      const sgOnly = !!c.same_game_only;
      const def = Math.min(parlayLegs, effMax);
      const sel = (id, opts, cur) => `<select id="${id}" style="width:auto;padding:2px 4px">`
        + opts.map(([v, lbl]) => `<option value="${v}"${v === cur ? " selected" : ""}>${lbl}</option>`).join("") + `</select>`;
      html += `<div class="combomaker">
        🎯 <b>Combo maker</b>
        <div class="small" style="margin:4px 0 2px">Pick which games (or a single team) the combo must come from — or <b>ALL GAMES</b>:</div>
        ${renderGameGrid(d.games)}
        <div style="margin-top:8px">each leg ≥
        <input id="comboTarget" type="number" min="20" max="97" value="${parlayTarget}" style="width:54px"/>% likely</div>
        <div class="small" style="margin-top:6px">
          ${sel("comboLegsMode", [["prefer", "recommend"], ["require", "require"], ["off", "off"]], comboLegsModePref)}
          <input id="comboN" type="number" min="2" max="12" value="${def}" style="width:50px"/> legs
          &nbsp;${sel("comboConn", [["or", "OR"], ["and", "AND"]], comboConnPref)}&nbsp;
          ${sel("comboPayoutMode", [["off", "off"], ["prefer", "recommend"], ["require", "require"]], comboPayoutModePref)}
          reach <input id="comboPayout" type="number" min="0" step="any" value="${parlayPayout}" style="width:60px"/>× payout
        </div>
        <label class="small" style="display:inline-block;margin-top:6px"><input type="checkbox" id="comboSameGame"${(comboSameGamePref || sgOnly) ? " checked" : ""}${sgOnly ? " disabled" : ""} style="width:auto"/> allow same-game parlays ${lockTag("mixed_parlay")}</label>${sgOnly ? `<div class="small" style="margin-top:4px;color:var(--muted)">Only one game on the slate today — combos stack correlated legs from that game, priced with the correlation-aware sim.</div>` : ""}
        &nbsp;<label class="small" style="display:inline-block"><input type="checkbox" id="comboLive"${comboIncludeLive ? " checked" : ""} style="width:auto" onchange="comboIncludeLive=this.checked"/> include live games (win legs only)</label>
        ${mlbTypeChipRow()}
        <button class="track-mini primary-mini" style="margin-top:6px" onclick="buildCombo()">Build</button>
        <div class="small" style="margin-top:4px">Each target (legs / payout) can be a hard <b>require</b>, a soft <b>recommend</b>, or <b>off</b>; combine them with <b>AND</b>/<b>OR</b>. Every line (hits, bases, runs total, ML, run line, RFI, Ks) is simulated. <b>Same-game on</b> may stack correlated legs from one game; off keeps one leg per game.</div>
        ${modelLegend()}
        <div id="comboOut"></div>
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
    if (c.mixed && c.mixed.length) {
      const mlabel = c.same_game_only
        ? `<b>🎲 Same-game combos</b> — one game on the slate, so these stack correlated legs (moneyline, totals &amp; props) from that game, priced with the correlation-aware sim:`
        : `<b>🎲 Mixed combos (incl. props)</b> — moneyline, run line, totals &amp; hit props, one leg per game:`;
      html += `<div class="small" style="margin:14px 0 4px">${mlabel}</div>`;
      html += c.mixed.map((x) => renderCombo(x)).join("");
    }
    // Preserve a built combo slip across the auto-refresh so it isn't wiped while
    // you're reading/screenshotting it. (Control values are restored from prefs.)
    const prevCombo = (() => { const el = $("comboOut"); return el ? el.innerHTML : ""; })();
    combosBox.innerHTML = html;
    if (prevCombo) { const el = $("comboOut"); if (el) el.innerHTML = prevCombo; }
    // Keep the Pick 6 board in sync with the loaded slate/date.
    if ($("bbPick6") && $("bbPick6").dataset.loaded && !$("bbPick6").classList.contains("hidden")) loadPick6();
  } catch (e) {
    gamesBox.innerHTML = `<div class="empty">Failed to load slate.</div>`;
    combosBox.innerHTML = "";
  }
}

function setupTabs() {
  $("bbetsBtn")?.addEventListener("click", loadBestBets);
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const tab = btn.dataset.tab;
      $("tab-bestbets").classList.toggle("hidden", tab !== "bestbets");
      $("tab-crypto").classList.toggle("hidden", tab !== "crypto");
      $("tab-baseball").classList.toggle("hidden", tab !== "baseball");
      $("tab-sports").classList.toggle("hidden", tab !== "sports");
      $("tab-nfl").classList.toggle("hidden", tab !== "nfl");
      $("tab-draft").classList.toggle("hidden", tab !== "draft");
      $("tab-ufc").classList.toggle("hidden", tab !== "ufc");
      $("tab-tennis").classList.toggle("hidden", tab !== "tennis");
      $("tab-worldcup").classList.toggle("hidden", tab !== "worldcup");
      $("tab-lol").classList.toggle("hidden", tab !== "lol");
      $("tab-commodities").classList.toggle("hidden", tab !== "commodities");
      $("tab-weather").classList.toggle("hidden", tab !== "weather");
      $("tab-sim").classList.toggle("hidden", tab !== "sim");
      $("tab-combine").classList.toggle("hidden", tab !== "combine");
      $("tab-ledger").classList.toggle("hidden", tab !== "ledger");
      if (tab === "bestbets" && !$("bbetsResults").dataset.loaded) {
        $("bbetsResults").dataset.loaded = "1";
        loadBestBets();
      }
      if (tab === "combine") loadCombineCats();
      if (tab === "sim") initSim();
      if (tab === "commodities" && !$("comResults").dataset.loaded) {
        $("comResults").dataset.loaded = "1";
        loadCommodities();
      }
      if (tab === "baseball") initBaseballTab();
      if (tab === "sports") initSportsTab();
      if (tab === "worldcup") initWorldCup();
      if (tab === "nfl") initNFL();
      if (tab === "ufc") initUFC();
      if (tab === "tennis") initTennis();
      if (tab === "lol") initLoL();
      if (tab === "draft") initDraft();
      if (tab === "weather" && !$("wxResults").dataset.loaded) {
        $("wxResults").dataset.loaded = "1";
        loadWeather();
      }
      if (tab === "ledger") loadLedger();
    });
  });
  setupBaseballSubtabs();
}

// ---- Best Bets: every net-of-fee edge across all models, one screen --------
const _SRC_LABEL = { mlb: "⚾ MLB slate", mlb_futures: "⚾ MLB futures", ufc: "🥊 UFC",
                     tennis: "🎾 Tennis", crypto: "⚡ Crypto", arbitrage: "🔒 Arbitrage" };
async function loadBestBets() {
  const box = $("bbetsResults"), src = $("bbetsSources");
  box.innerHTML = `<div class="empty">Hunting edges across every model… (the MLB slate sim is the slow part — ~30s cold)</div>`;
  src.innerHTML = "";
  try {
    const d = await (await fetch("/api/bestbets")).json();
    if (d.error) { box.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    // Source status chips: which models contributed, which failed/are warming.
    src.innerHTML = Object.entries(d.sources || {}).map(([k, s]) =>
      `<span class="leanchip${s.ok ? "" : " warn"}" title="${s.ok ? "" : (s.error || "unavailable")}">${_SRC_LABEL[k] || k}: ${s.ok ? `<b>${s.rows}</b>` : "✗"}</span>`).join(" ");
    if (!d.rows || !d.rows.length) {
      box.innerHTML = `<div class="empty">No positive net-of-fee edges right now — that's an honest answer, not a bug. Markets tighten close to game time; check back when slates/cards post.</div>`;
      return;
    }
    const head = `<div class="edgerow edgehead">
      <span class="ecol-edge">Net</span><span class="ecol-pick">Bet</span>
      <span class="ecol-num">Our %</span><span class="ecol-num">Ask</span>
      <span class="ecol-num">Pays</span><span class="ecol-conf">Trust</span></div>`;
    const body = d.rows.map((r) => {
      const stake = stakeText(r.our_pct / 100, r.cents) || "";
      const note = r.note ? `<span class="emu">⚠ ${r.note}</span>` : "";
      return `<div class="edgerow">
        <span class="ecol-edge ev pos">+${r.net_edge}<span class="enet">gross +${r.edge}</span></span>
        <span class="ecol-pick"><span class="legtag">${r.sport}</span> <b>${r.pick}</b><span class="emu">${r.kind} · ${r.matchup}</span>${note}${stake ? `<span class="emu">${stake}</span>` : ""}</span>
        <span class="ecol-num">${r.our_pct}%</span>
        <span class="ecol-num">${r.cents}¢</span>
        <span class="ecol-num">${r.payout_x ? r.payout_x + "×" : "—"}</span>
        <span class="ecol-conf conf-${r.trust}">${CONF_LABEL[r.trust] || r.trust}</span>
      </div>`;
    }).join("");
    box.innerHTML = head + body +
      `<div class="small" style="margin-top:10px;color:var(--muted)">${d.rows.length} bets from ${d.n_candidates} candidates · net = our % − ask − Kalshi taker fee · capped 3 per matchup so one opinion can't flood the board. <b>Trust</b>: soft rows are usually our model's bias, not the market's mistake.</div>`;
  } catch (e) {
    box.innerHTML = `<div class="empty">Best Bets scan failed — try Rescan.</div>`;
  }
}

// Baseball hub: Slate & combos / Edges / Hits as sub-views (formerly separate tabs).
function initBaseballTab() {
  if (!$("bbGames").dataset.loaded) { $("bbGames").dataset.loaded = "1"; loadBaseball(); }
}
function setupBaseballSubtabs() {
  document.querySelectorAll("#tab-baseball .subtab[data-bbsub]").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll("#tab-baseball .subtab[data-bbsub]").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      const s = b.dataset.bbsub;
      document.querySelector("#tab-baseball .bb-slate").classList.toggle("hidden", s !== "slate");
      document.querySelector("#tab-baseball .bb-edges").classList.toggle("hidden", s !== "edges");
      document.querySelector("#tab-baseball .bb-hits").classList.toggle("hidden", s !== "hits");
      if (s === "edges" && !$("edgeDate").dataset.loaded) { $("edgeDate").dataset.loaded = "1"; initEdges(); }
      if (s === "hits" && !$("hitsDate").dataset.loaded) { $("hitsDate").dataset.loaded = "1"; initHits(); }
    });
  });
  // Combo maker mode: Kalshi combos vs DraftKings Pick 6.
  document.querySelectorAll("#comboMode .subtab").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll("#comboMode .subtab").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      const p6 = b.dataset.cmode === "pick6";
      $("bbCombos").classList.toggle("hidden", p6);
      $("comboKalshiSub").classList.toggle("hidden", p6);
      $("bbPick6").classList.toggle("hidden", !p6);
      $("comboPick6Sub").classList.toggle("hidden", !p6);
      if (p6 && !$("bbPick6").dataset.loaded) { $("bbPick6").dataset.loaded = "1"; loadPick6(); }
    });
  });
}

// ---- DraftKings Pick 6 builder -------------------------------------------
// Pick 6 browser: pick a game -> every player's simulated averages + the More%
// at every line -> click lines to build a slip. Joint odds come from the server
// (masks ANDed on the shared sim), since same-game legs are correlated.
let _p6b = { games: [], pk: null, players: [], payouts: {}, slip: [] };
async function loadPick6(pk) {
  pk = pk || _p6b.pk;
  const box = $("bbPick6");
  const date = $("bbDate").value;
  if (!_p6b.games.length) box.innerHTML = `<div class="empty">Simulating the game (4000 runs)…</div>`;
  try {
    const d = await (await fetch(`/api/baseball/pick6/sheet?date=${date}${pk ? `&pk=${pk}` : ""}`)).json();
    if (d.error) { box.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    const keepSlip = (_p6b.pk === d.pk) ? _p6b.slip : [];   // slip is per-game
    _p6b = { ...d, slip: keepSlip };
    renderP6Browser();
    p6Eval();
  } catch (e) { box.innerHTML = `<div class="empty">Pick 6 unavailable.</div>`; }
}
function p6on(player, t, n, side) {
  return _p6b.slip.some((l) => l.player === player && l.t === t && l.n === n && l.side === side);
}
function renderP6Browser() {
  const box = $("bbPick6");
  const opts = _p6b.games.map((g) =>
    `<option value="${g.pk}"${g.pk === _p6b.pk ? " selected" : ""}>${g.matchup}${g.live ? " · LIVE" : ""}</option>`).join("");
  const rows = (_p6b.players || []).map((pl, pi) => {
    const stats = pl.stats.map((st, si) => {
      const chips = st.lines.map((ln, li) => {
        let h = `<button class="p6chip${p6on(pl.player, st.t, ln.n, "More") ? " on" : ""}" onclick="p6add(${pi},${si},${li},'More')">${ln.line}+ <b>${ln.more_pct}%</b></button>`;
        if (st.t === "ks")   // DK offers a Less side on pitcher Ks only
          h += `<button class="p6chip lessc${p6on(pl.player, st.t, ln.n, "Less") ? " on" : ""}" onclick="p6add(${pi},${si},${li},'Less')">&lt;${ln.line} <b>${Math.round((100 - ln.more_pct) * 10) / 10}%</b></button>`;
        return h;
      }).join("");
      return `<div class="p6statline"><span class="p6statlbl">${st.label} <b>${st.avg != null ? st.avg : "—"}</b><span class="small" style="color:var(--faint)"> avg</span></span><span class="p6chips">${chips}</span></div>`;
    }).join("");
    return `<div class="p6prow"><div class="p6pname">${pl.kind === "P" ? "🧢 " : ""}${pl.player}</div>${stats}</div>`;
  }).join("");
  box.innerHTML = `
    <div class="scan-controls" style="margin-bottom:8px">
      <select id="p6game" onchange="loadPick6(parseInt(this.value,10))">${opts}</select>
      <span class="small" style="color:var(--muted)">${nf(_p6b.n_sims)} sims · avg = what the sim expects · click a line to add it to your slip</span>
    </div>
    <div id="p6slip" class="p6tally"></div>
    <div class="p6sheet">${rows || `<div class="empty">No simmable players yet — batter props post with lineups (a few hours pre-game).</div>`}</div>`;
  renderP6Slip();
}
function p6add(pi, si, li, side) {
  const pl = _p6b.players[pi], st = pl.stats[si], ln = st.lines[li];
  const prob = side === "More" ? ln.more_pct : Math.round((100 - ln.more_pct) * 10) / 10;
  const leg = { player: pl.player, t: st.t, label: st.label, n: ln.n, line: ln.line, side, prob };
  const i = _p6b.slip.findIndex((l) => l.player === pl.player && l.t === st.t);
  if (i >= 0 && _p6b.slip[i].n === ln.n && _p6b.slip[i].side === side) _p6b.slip.splice(i, 1);  // toggle off
  else if (i >= 0) _p6b.slip[i] = leg;              // same player+stat -> switch the line
  else if (_p6b.slip.length < 6) _p6b.slip.push(leg);
  else return;                                      // 6-leg cap (DK Pick 6)
  renderP6Browser();
  p6Eval();
}
function p6rm(i) { _p6b.slip.splice(i, 1); renderP6Browser(); p6Eval(); }
function renderP6Slip(ev) {
  const t = $("p6slip");
  if (!t) return;
  const n = _p6b.slip.length;
  if (!n) {
    t.innerHTML = `<span class="small">🎯 <b>Your slip</b> — click lines below (2–6 picks, all must hit). The % on each chip is the sim's chance at that exact line; DK's posted line governs.</span>`;
    return;
  }
  const legs = _p6b.slip.map((l, i) =>
    `<span class="p6slipleg"><span class="p6side ${l.side === "More" ? "more" : "less"}">${l.side} ${l.line}</span> ${l.player} <span class="p6stat">${l.label}</span> <b>${l.prob}%</b><button class="p6x" onclick="p6rm(${i})" title="remove">×</button></span>`).join("");
  let tally;
  if (n >= 2) {
    const pay = _p6b.payouts[String(n)];
    if (ev && ev.joint_pct != null) {
      const evPct = pay ? Math.round((ev.joint_pct / 100 * pay - 1) * 100) : null;
      tally = ` · joint <b>${ev.joint_pct}%</b> <span class="small" style="color:var(--muted)" title="what independent multiplication would claim — same-game legs are correlated, the joint number is the real one">(indep ${ev.indep_pct}%)</span>${pay ? ` · pays <b>${pay}×</b> · EV <b class="${evPct >= 0 ? "ev pos" : "ev neg"}">${evPct >= 0 ? "+" : ""}${evPct}%</b>` : ""}`;
    } else tally = ` · <span class="small" style="color:var(--muted)">computing correlated joint odds…</span>`;
  } else tally = ` · <span class="small">add ${2 - n} more for a payable slip</span>`;
  t.innerHTML = `<b>${n}-pick</b>${tally}<div class="p6sliplegs">${legs}</div>`;
}
async function p6Eval() {
  renderP6Slip();
  if (_p6b.slip.length < 2) return;
  try {
    const body = { date: $("bbDate").value, pk: _p6b.pk,
                   legs: _p6b.slip.map((l) => ({ t: l.t, player: l.player, n: l.n, side: l.side })) };
    const d = await (await fetch("/api/baseball/pick6/eval", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
    renderP6Slip(d.error ? null : d);
  } catch (e) { /* leave the independent view */ }
}
function abbrMatch(mu) {
  if (!mu) return "";
  if (mu.includes(" @ ")) return mu.split(" @ ").map((t) => t.split(" ").pop()).join("@");
  return mu;
}

// ---- Predicted Hits / Risky Hits -----------------------------------------
function initHits() {
  const sel = $("hitsDate");
  if (!sel) return;
  const today = new Date();
  let opts = "";
  for (let i = 0; i < 8; i++) {
    const d = new Date(today); d.setDate(today.getDate() - i);
    const iso = d.toISOString().slice(0, 10);
    opts += `<option value="${iso}">${i === 0 ? "Today" : i === 1 ? "Yesterday" : iso}</option>`;
  }
  opts += `<option value="">All time</option>`;
  sel.innerHTML = opts;
  loadHits();
}

async function loadHits() {
  const box = $("hitsResults");
  if (!box) return;
  box.innerHTML = `<div class="empty">Loading the board…</div>`;
  try {
    const date = ($("hitsDate") || {}).value || "";
    const d = await (await fetch(`/api/baseball/hits?date=${date}`)).json();
    if (d.error) { box.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    box.innerHTML = renderHits(d);
  } catch (e) {
    box.innerHTML = `<div class="empty">Couldn't load the board.</div>`;
  }
}


// A cashed hindsight combo card: every leg hit, here's what the parlay paid.
function renderHitCombo(c, tag, extraCls) {
  const legs = c.legs.map((l) =>
    `<li>✅ <span class="legtag">${l.type}</span> ${l.pick}
      <span style="color:var(--muted)">(model ${l.prob_pct != null ? l.prob_pct + "%" : "—"}, ${l.price_cents}¢ → ${l.payout_x}×)</span></li>`).join("");
  return `<div class="combo ${extraCls || ""}">
    <div class="chead">
      <span class="ctag">${tag}</span>
      <span class="small">${c.n_legs} legs · ${c.date || ""}</span>
    </div>
    <ul class="legs">${legs}</ul>
    <div class="cnums">
      <span>Combined chance <b>${c.combined_prob_pct != null ? c.combined_prob_pct + "%" : "—"}</b></span>
      <span>Parlay paid <b class="ev pos">${c.parlay_payout_x}×</b></span>
      <span>$5 → <b class="ev pos">$${c.ret_5.toLocaleString()}</b></span>
      <span>$10 → <b class="ev pos">$${c.ret_10.toLocaleString()}</b></span>
    </div>
  </div>`;
}

function renderHits(d) {
  if (!d.graded_n) {
    const rec = d.recorder || {};
    return `<div class="empty">Nothing graded yet for this slate.<br>
      <span class="small">The recorder logs props every ~10 min and grades them once games go final${rec.logged != null ? ` (so far: ${rec.logged} logged)` : ""}. Combos appear here once a slate finishes. Check back later tonight.</span></div>`;
  }
  const s = d.predicted_summary || {};
  const sumLine = s.recommended
    ? `<div class="small" style="margin:2px 0 8px">Of <b>${s.recommended}</b> props the model liked (≥55%), <b class="${(s.hit_pct||0) >= 50 ? "ev pos" : "ev neg"}">${s.hit}</b> hit (${s.hit_pct}%). Honest record.</div>`
    : "";
  const predicted = (d.predicted_combos || []).length
    ? d.predicted_combos.map((c, i) => renderHitCombo(c, i === 0 ? "🛡️ Best model combo that cashed" : "✅ Model combo that cashed", "hl")).join("")
    : `<div class="small">No multi-leg model combo cashed for this slate yet (need ≥2 model-liked props hitting in different games).</div>`;
  const moon = d.moonshot
    ? renderHitCombo(d.moonshot, "🚀 Moonshot — longshots that all cashed", "hl prop")
    : `<div class="small">No longshot moonshot cashed this slate (or none graded yet).</div>`;
  return `
    <div class="hitsec"><div class="hitsechead">🎯 Predicted combos — what the model liked, that cashed</div>
      ${sumLine}${predicted}</div>
    <div class="hitsec"><div class="hitsechead">🍀 Risky moonshot — the few-dollars-to-thousands combo</div>
      <div class="small" style="margin:2px 0 8px">The cheapest YES longshots that all hit on one slate, parlayed. Pure hindsight — what it <i>would</i> have paid, not advice.</div>
      ${moon}</div>`;
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

// ---- Edge Finder ----------------------------------------------------------
let _edgeData = null;

function initEdges() {
  const sel = $("edgeDate");
  if (!sel) return;
  const today = new Date();
  let opts = "";
  for (let i = 0; i < 4; i++) {
    const d = new Date(today); d.setDate(today.getDate() + i);
    const iso = d.toISOString().slice(0, 10);
    opts += `<option value="${iso}">${i === 0 ? "Today" : i === 1 ? "Tomorrow" : iso}</option>`;
  }
  sel.innerHTML = opts;
  $("edgeMin").onchange = renderEdges;
  $("edgeSide").onchange = renderEdges;
  loadEdges();
}

async function loadEdges() {
  const box = $("edgeResults");
  const date = $("edgeDate").value;
  if ($("edgeTypeChips")) $("edgeTypeChips").innerHTML = "Prop types <span style='color:var(--muted)'>(none = all, then re-scan)</span>: " + mlbTypeChipsHTML();
  box.innerHTML = `<div class="empty">Scanning every priced leg across the slate… (simulating each game, a few seconds)</div>`;
  $("edgeSummary").innerHTML = "";
  try {
    const d = await (await fetch(`/api/baseball/edges?date=${date}&min_edge=4${mlbTypesParam()}`)).json();
    if (d.error) { box.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    _edgeData = d;
    renderEdges();
  } catch (e) {
    box.innerHTML = `<div class="empty">Scan failed: ${e}</div>`;
  }
}

const CONF_LABEL = { high: "trusted", med: "medium", low: "soft" };

function renderEdges() {
  const d = _edgeData;
  if (!d) return;
  const box = $("edgeResults");
  if (!d.edges || !d.edges.length) {
    box.innerHTML = `<div class="empty">No legs priced yet — Kalshi posts most props/totals closer to game time. Try again nearer first pitch.</div>`;
    return;
  }
  // Per-market lean banner: flags systematic model bias vs one-off edges.
  const leaned = Object.entries(d.summary).filter(([, s]) => s.lean);
  const chips = Object.entries(d.summary)
    .sort((a, b) => b[1].count - a[1].count)
    .map(([t, s]) => `<span class="leanchip${s.lean ? " warn" : ""}">${t}: <b>${s.pos}↑</b>/<b>${s.neg}↓</b> avg ${s.avg_edge >= 0 ? "+" : ""}${s.avg_edge}${s.lean ? " ⚠" : ""}</span>`)
    .join("");
  let banner = `<div class="leanrow">${chips}</div>`;
  if (leaned.length) {
    banner += `<div class="leanwarn">⚠ ${leaned.map(([t]) => t).join(", ")} lean almost entirely one way across the slate — that's usually <b>our model</b> being systematically off on that market, not ${leaned.length > 1 ? "those" : "that"} being real edges. Treat with skepticism.</div>`;
  }
  $("edgeSummary").innerHTML = banner;

  const minEdge = parseFloat($("edgeMin").value);
  const side = $("edgeSide").value;
  let rows = d.edges.filter((r) => Math.abs(r.edge) >= minEdge);
  if (side === "pos") rows = rows.filter((r) => r.edge > 0);
  else if (side === "neg") rows = rows.filter((r) => r.edge < 0);
  // Rank by the edge you can actually trade (net of Kalshi's taker fee).
  rows.sort((a, b) => (b.net_edge ?? b.edge) - (a.net_edge ?? a.edge));
  if (!rows.length) { box.innerHTML = `<div class="empty">Nothing at that filter. Lower the min edge or change the side.</div>`; return; }

  const head = `<div class="edgerow edgehead">
    <span class="ecol-edge">Edge</span><span class="ecol-pick">Leg</span>
    <span class="ecol-num">Our %</span><span class="ecol-num">Kalshi</span>
    <span class="ecol-num">Pays</span><span class="ecol-conf">Trust</span></div>`;
  const body = rows.map((r) => {
    const cls = r.edge >= 0 ? "pos" : "neg";
    const model = (r.model_pct != null) ? ` <span class="emodel">model ${r.model_pct}%</span>` : "";
    const net = (r.net_edge != null)
      ? `<span class="enet" title="after Kalshi's ~${r.fee_cents}¢ taker fee">net ${r.net_edge >= 0 ? "+" : ""}${r.net_edge}</span>` : "";
    return `<div class="edgerow">
      <span class="ecol-edge ev ${cls}">${r.edge >= 0 ? "+" : ""}${r.edge}${net}</span>
      <span class="ecol-pick"><b>${r.pick}</b><span class="emu">${r.matchup}</span></span>
      <span class="ecol-num">${r.our_pct}%${model}</span>
      <span class="ecol-num">${r.market_cents}¢</span>
      <span class="ecol-num">${r.market_payout_x}×</span>
      <span class="ecol-conf conf-${r.confidence}">${CONF_LABEL[r.confidence] || r.confidence}</span>
    </div>`;
  }).join("");
  box.innerHTML = head + body +
    `<div class="small" style="margin-top:10px;color:var(--muted)">Showing ${rows.length} of ${d.n_priced} priced legs. <b>Edge</b> = our simulated chance − Kalshi's price; the small <b>net</b> figure subtracts Kalshi's ~1–2¢ taker fee (the edge you actually bank). <b>Trust</b> reflects how well-grounded the model is for that market (Ks/Total/ML strongest). Positive edge = we think YES is likelier than the market.</div>`;
}

async function runBacktest() {
  if (!isOwner()) return;   // backtest is owner-only data
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
  if (!isOwner()) return;   // crypto recorder log is owner-only data
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

// Collapse an outcome list to the first 3 with a "See N more" toggle, but only
// when a SINGLE market has more than 3 outcomes (e.g. a 40-driver race winner).
let _moreSeq = 0;
function collapseRows(rows, label) {
  if (rows.length <= 3) return rows.join("");
  const id = "more" + (_moreSeq++);
  const extra = rows.length - 3;
  return rows.slice(0, 3).join("") +
    `<div id="${id}" class="hidden">${rows.slice(3).join("")}</div>` +
    `<button class="track-mini seemore" onclick="toggleMore('${id}',this,${extra},'${label || "more"}')">▾ See ${extra} ${label || "more"}</button>`;
}
window.toggleMore = (id, btn, n, label) => {
  const el = document.getElementById(id);
  if (!el) return;
  const hidden = el.classList.toggle("hidden");
  btn.innerHTML = hidden ? `▾ See ${n} ${label}` : "▴ Show less";
};

function renderSportEvent(e, sportKey) {
  const secs = e.close_time ? e.close_time - Math.floor(Date.now() / 1000) : 0;
  const vig = e.overround_pct;
  const vigCls = vig == null ? "" : vig <= 4 ? "ev pos" : vig >= 10 ? "ev neg" : "";
  const outRows = e.outcomes.map((o) => {
    const f = sfid(o.ticker);
    let modelStr = "";
    if (o.model_pct != null) {
      const ec = o.edge_cents;
      const ecls = ec > 0 ? "ev pos" : ec < 0 ? "ev neg" : "";
      modelStr = ` · model <b>${o.model_pct}%</b>${ec != null ? ` · edge <b class="${ecls}">${ec > 0 ? "+" : ""}${ec}¢</b>` : ""}`;
    }
    // Spread chip: flags a wide/untradeable quote behind the "fair %".
    const sp = o.spread;
    const spStr = (sp != null)
      ? ` · <span class="${sp >= 10 ? "ev neg" : ""}" title="bid-ask spread">spread ${sp}¢</span>`
      : ` · <span class="ev neg" title="no two-sided quote">no bid</span>`;
    return `<div class="sportout">
      <div class="left">
        <span class="oname">${o.name}</span>
        <span class="small">Kalshi <b>${o.yes_ask != null ? o.yes_ask + "¢" : "—"}</b> · no-vig fair <b>${o.fair_pct != null ? o.fair_pct + "%" : "—"}</b>${spStr}${modelStr}</span>
      </div>
      <button class="track-mini" id="${f}_btn" onclick="showSportLog('${o.ticker}')">Log</button>
      <div class="buyform hidden" id="${f}">
        $<input id="${f}_stake" type="number" step="any" min="0" placeholder="stake" style="width:70px"/>
        <button class="track-mini primary-mini" onclick='logSportBet(${JSON.stringify(o.ticker)},${JSON.stringify(sportKey)},${JSON.stringify(e.title)},${JSON.stringify(o.name)},${o.yes_ask})'>Save</button>
      </div>
    </div>`;
  });
  const outs = collapseRows(outRows, "more");
  // Liquidity gate: in thin/untraded books the fair %, edge and "arbitrage" are
  // mirages off stale quotes, so warn and suppress the misleading callouts.
  const thin = e.liquidity === "thin" || e.liquidity === "none";
  const liqWarn = thin
    ? `<div class="note" style="border:1px solid var(--no);color:var(--no)">⚠ ${e.liquidity === "none" ? "Untraded / one-sided book" : "Thin market"} (${e.volume || 0} contracts) — the no-vig fair %, edges and any "arbitrage" here come off stale, wide quotes and aren't reliably tradeable.</div>`
    : "";
  const arb = (e.arbitrage_pct && !thin)
    ? `<div class="note dip" style="border-color:var(--yes);color:var(--yes)">💸 Arbitrage: outcome prices sum to ${(100 - e.arbitrage_pct).toFixed(1)}¢ — buying every outcome locks in ~${e.arbitrage_pct}¢ guaranteed profit per $1.</div>`
    : "";
  // Racing: the model edge pick beats the market-favorite lean when present.
  const mp = e.model_pick
    ? `<div class="note" style="border:1px solid var(--yes);color:var(--yes)">🏁 Model edge pick: <b>${e.model_pick.name}</b> @ ${e.model_pick.yes_ask}¢ — model <b>${e.model_pick.model_pct}%</b> vs market, <b>+${e.model_pick.edge_cents}¢ edge</b></div>`
    : "";
  const pick = (e.pick && !thin)
    ? `<div class="note" style="border:1px solid var(--accent);color:var(--accent)">✅ Buy this one: <b>${e.pick.name}</b> @ ${e.pick.yes_ask}¢ · <b>${e.pick.fair_pct}%</b> confidence (market favorite)</div>`
    : "";
  return `<div class="bbgame">
    <div class="top">
      <div class="matchup">${e.title}</div>
      <div class="small" style="text-align:right">closes ${fmtCountdown(secs)}<br>${vig != null ? `vig <b class="${vigCls}">${vig}%</b>` : ""}</div>
    </div>
    ${liqWarn}
    ${mp}
    ${pick}
    ${arb}
    <div class="sportouts">${outs}</div>
  </div>`;
}

// ---- Sports tab: Live Now / Featured / All Sports sub-views ----------------
let _sportsSubInit = false, _liveLoaded = false, _featLoaded = false;
function initSportsTab() {
  if (!_sportsSubInit) {
    document.querySelectorAll("#tab-sports .subtab").forEach((b) => {
      b.addEventListener("click", () => {
        document.querySelectorAll("#tab-sports .subtab").forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
        const s = b.dataset.sub;
        document.querySelector("#tab-sports .sub-live").classList.toggle("hidden", s !== "live");
        document.querySelector("#tab-sports .sub-featured").classList.toggle("hidden", s !== "featured");
        document.querySelector("#tab-sports .sub-all").classList.toggle("hidden", s !== "all");
        if (s === "live" && !_liveLoaded) { _liveLoaded = true; loadLive(); }
        if (s === "featured" && !_featLoaded) { _featLoaded = true; loadFeatured(); }
        if (s === "all" && !$("sportResults").dataset.loaded) { $("sportResults").dataset.loaded = "1"; loadSports(); }
      });
    });
    _sportsSubInit = true;
  }
  if (!_liveLoaded) { _liveLoaded = true; loadLive(); }   // default sub-view
}

async function loadLive() {
  const box = $("liveResults");
  box.innerHTML = `<div class="empty">Scanning live games across every sport…</div>`;
  try {
    const d = await (await fetch("/api/sports/live")).json();
    if (!d.games || !d.games.length) {
      box.innerHTML = `<div class="empty">No tracked games are live right now. Any MLB, WNBA, NHL, NBA, NFL, college football, or soccer game we track appears here the moment it tips off, with the live score and game clock.</div>`;
      return;
    }
    const conf = d.games.filter((g) => g.confirmed);
    const inf = d.games.filter((g) => !g.confirmed);
    _liveNavs = d.games.map((g) => g.nav || null);
    const navi = (g) => { const i = d.games.indexOf(g); return _liveNavs[i] ? ` golive" data-navi="${i}` : ""; };
    let h = "";
    if (conf.length) {
      h += `<div class="teamhdr">🔴 Confirmed live</div>`;
      h += conf.map((g) => `<div class="liverow${navi(g)}"><b>${g.sport}</b> ${g.title}
        <span class="livescore">${g.score || ""}</span> <span class="small">${g.detail || ""}</span>${_liveNavs[d.games.indexOf(g)] ? '<span class="small" style="color:var(--muted)"> · tap to open ➜</span>' : ""}</div>`).join("");
    }
    if (inf.length) {
      h += `<div class="teamhdr" style="margin-top:12px">⏳ Likely in-play <span class="small">(inferred from Kalshi market timing — approximate)</span></div>`;
      h += inf.map((g) => `<div class="liverow"><b>${g.sport}</b> ${g.title}
        <span class="small">${g.detail || ""}</span>${g.fav ? ` · lean <b>${g.fav.name}</b> ${g.fav.fair}%` : ""}</div>`).join("");
    }
    box.innerHTML = h;
    box.querySelectorAll("[data-navi]").forEach((el) =>
      el.addEventListener("click", () => liveNavGo(_liveNavs[+el.dataset.navi])));
  } catch (e) {
    box.innerHTML = `<div class="empty">Couldn't load live games.</div>`;
  }
}

let _liveNavs = [];
// Jump from a live row to its game: switch to the right tab, wait for that
// board to render, then scroll the matching card into view and flash it.
function liveNavGo(nav) {
  if (!nav) return;
  const btn = document.querySelector(`.tab[data-tab="${nav.tab}"]`);
  if (!btn) return;
  btn.click();
  if (nav.tab === "tennis") {
    const s = $("tnSearch");
    if (s) { s.value = nav.q || ""; }
  }
  const t0 = Date.now();
  const find = () => {
    let el = null;
    if (nav.pk) el = document.querySelector(`.bbgame[data-pk="${nav.pk}"]`);
    else if (nav.tab === "tennis") {
      if (_tnData) renderTennis();
      el = document.querySelector("#tnResults .tn-match");
    } else if (nav.q) {
      const root = $("tab-" + nav.tab);
      const words = nav.q.toLowerCase().split("|");
      el = Array.from(root ? root.querySelectorAll("div") : []).find((x) =>
        x.offsetHeight > 0 && x.offsetHeight < 500 && x.children.length &&
        words.every((w) => (x.textContent || "").toLowerCase().includes(w)));
    }
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("nav-flash");
      setTimeout(() => el.classList.remove("nav-flash"), 3000);
    } else if (Date.now() - t0 < 15000) {
      setTimeout(find, 700);      // board may still be loading — retry
    }
  };
  setTimeout(find, 450);
}

let _boardData = null, _featMarket = null, _featSport = "mlb";
let _deepTimer = null, _featGenTime = 0;   // generation time of the board on screen
const _featIntro = {
  mlb: `Our season Monte Carlo vs the market — division / playoff / pennant / World Series odds and win totals, vs Kalshi & Polymarket. Click "Run deep sim" for the pitch-by-pitch engine.`,
  f1: `Deep F1 season sim — every remaining weekend we simulate qualifying (the grid/pole), the race, and sprints, award points, and roll the season forward. Title odds, projected points, expected wins/poles/podiums + constructors.`,
  nfl: `NFL season Monte Carlo — every remaining game simulated 4,000 times off roster-aware team projections → Super Bowl / conference / division / playoff odds and win totals vs Kalshi's futures. Click a team for its Sleeper-projected player stat lines (real stats blend in as the season plays).`,
  nascar: `Deep NASCAR Cup sim — pace + points from this season's races, then the full playoff bracket (Round of 16 → 12 → 8 → Championship 4). The winner-take-all finale keeps title odds flat, as in real NASCAR.`,
};
function setFeatSport(s) {
  _featSport = s;
  document.querySelectorAll("#tab-sports .sportbtn").forEach((b) =>
    b.classList.toggle("active", b.dataset.fsport === s));
  const intro = $("featuredIntro"); if (intro) intro.innerHTML = _featIntro[s];
  loadFeatured();
}

let _featReq = 0;   // increments on every load; a stale fetch can't paint over a newer sport
async function loadFeatured(force) {
  const box = $("featuredResults");
  const sport = _featSport;                 // pin the sport this request is for
  const my = ++_featReq;                    // this request's token
  // `force` (Refresh button) appends a cache-buster so the browser re-fetches
  // from the server instead of serving a stale HTTP-cached response. The server
  // board itself is still its normal cached run (fast) — this fixes a wrong/
  // stale board on screen without kicking a slow recompute.
  const bust = force ? `_=${Date.now()}` : "";
  box.innerHTML = `<div class="empty">Simulating the season…</div>`;
  $("featuredSummary").innerHTML = "";
  // A stale render finished for a DIFFERENT sport (or a newer request superseded
  // this one) -> drop it on the floor instead of painting the wrong board.
  const stale = () => my !== _featReq || sport !== _featSport;
  try {
    if (sport === "mlb") {
      const d = await (await fetch("/api/baseball/futures" + (bust ? "?" + bust : ""))).json();
      if (stale()) return;
      if (d.error) { box.innerHTML = `<div class="empty">${d.error}</div>`; return; }
      _boardData = d; _featGenTime = _genTime(d.age_sec); renderFeatured(d);
    } else if (sport === "nfl") {
      const d = await (await fetch("/api/nfl/futures" + (bust ? "?" + bust : ""))).json();
      if (stale()) return;
      if (d.error) { box.innerHTML = `<div class="empty">${d.error}</div>`; return; }
      _boardData = d; renderFeatured(d);
    } else {
      const d = await (await fetch(`/api/racing/${sport}` + (bust ? "?" + bust : ""))).json();
      if (stale()) return;
      if (d.error) { box.innerHTML = `<div class="empty">${d.error}</div>`; return; }
      _featGenTime = _genTime(d.age_sec); renderRacing(d);
    }
    watchFeatured();   // drive the progress bar + auto-reload when a newer run lands
  } catch (e) {
    if (!stale()) box.innerHTML = `<div class="empty">Season sim failed.</div>`;
  }
}

function agoStr(sec) {
  if (sec == null) return "never";
  if (sec < 90) return "just now";
  if (sec < 5400) return `${Math.round(sec / 60)} min ago`;
  if (sec < 172800) return `${Math.round(sec / 3600)} h ago`;
  return `${Math.round(sec / 86400)} d ago`;
}
async function rerunSim(sport, after) {
  await fetch(`/api/sim/rerun?sport=${sport}`, { method: "POST" });
  const tick = async () => {
    const s = (await (await fetch("/api/sim/status")).json())[sport === "mlb" ? "mlb_deep" : sport];
    if (s && s.running) { setTimeout(tick, 2500); return; }
    if (after) after();
  };
  setTimeout(tick, 2000);
}

let _raceData = null, _raceMarket = "drivers";
function renderRacing(d) {
  _raceData = d;
  const isF1 = d.sport === "f1";
  const fresh = `updated <b>${agoStr(d.age_sec)}</b> · reruns nightly (auto-updates here)`;
  $("featuredSummary").innerHTML =
    `<div class="small" style="margin-bottom:8px">Simulated <b>${d.n_sims.toLocaleString()}</b> seasons · <b>${d.races_left}</b> races left · ${isF1 ? "qualifying + race + sprints, with grid penalties / DNFs / time penalties" : "pace + the playoff bracket, with wrecks / incidents / penalties"}.
     <span style="color:var(--muted)">${fresh}</span>
     <button class="track-mini" style="margin-left:6px" onclick="rerunSim('${d.sport}', loadFeatured)">↻ rerun now</button></div>`;
  const races = d.races || [];
  // Validate the remembered market against this sport's data.
  if (_raceMarket === "constructors" && !isF1) _raceMarket = "drivers";
  if (/^(win|pole):/.test(_raceMarket) && !races[+_raceMarket.split(":")[1]]) _raceMarket = "drivers";
  let opts = `<optgroup label="Championship"><option value="drivers">Drivers' Championship</option>`;
  if (isF1) opts += `<option value="constructors">Constructors' Championship</option>`;
  opts += `</optgroup><optgroup label="Race winner">`
    + races.map((r, i) => `<option value="win:${i}">${r.name}${r.sprint ? " (sprint wknd)" : ""}</option>`).join("")
    + `</optgroup><optgroup label="Pole position">`
    + races.map((r, i) => `<option value="pole:${i}">${r.name}</option>`).join("") + `</optgroup>`;
  $("featuredResults").innerHTML = `
    <div class="futctl"><label class="small">Market</label>
      <select id="raceMarket" onchange="_raceMarket=this.value;renderRaceMarket()">${opts}</select></div>
    <div id="raceTable"></div>`;
  $("raceMarket").value = _raceMarket;
  renderRaceMarket();
}

// ---- NFL futures projection ----
let _nflData = null, _nflSub = "futures", _nflPoll = null;
function initNFL() {
  initNFLWeek();                          // week board is the default view
  document.querySelectorAll("#nflSubtabs .subtab").forEach((b) => {
    if (b.dataset.wired) return;
    b.dataset.wired = "1";
    b.addEventListener("click", () => {
      document.querySelectorAll("#nflSubtabs .subtab").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      const s = b.dataset.nflsub;
      const simViews = ["sim", "best", "pick6"];
      $("nflWeekBox").classList.toggle("hidden", s !== "week");
      $("nflSimBox").classList.toggle("hidden", !simViews.includes(s));
      $("nflFutBox").classList.toggle("hidden", s === "week" || simViews.includes(s));
      if (s === "week") { initNFLWeek(); return; }
      if (simViews.includes(s)) { _nflSimView = s; initNFLSim(); return; }
      _nflSub = s;                         // "futures" | "wins" share the projection load
      if (!$("nflResults").dataset.loaded) { $("nflResults").dataset.loaded = "1"; loadNFL(); }
      else renderNFL();
    });
  });
}

// ---- NFL week board (modeled scores / yards / TDs) -------------------------
let _nflWeekData = null;
function initNFLWeek() {
  const sel = $("nflWeek");
  if (sel && !sel.dataset.filled) {
    sel.dataset.filled = "1";
    let opts = "";
    for (let w = 1; w <= 18; w++) opts += `<option value="${w}">Week ${w}</option>`;
    sel.innerHTML = opts;
    sel.addEventListener("change", () => {
      _nflWeekData = null;
      $("nflWeekResults").dataset.loaded = "";
      loadNFLWeek(0);
    });
  }
  if (!$("nflWeekResults").dataset.loaded) { $("nflWeekResults").dataset.loaded = "1"; loadNFLWeek(0); }
}
async function loadNFLWeek(attempt) {
  attempt = attempt || 0;
  const box = $("nflWeekResults"), wk = ($("nflWeek") || {}).value || 1;
  if (!attempt) box.innerHTML = `<div class="empty">Building the Week ${wk} board in the background — ESPN team ratings + per-team stats + key players (~15s). Auto-refreshes.</div>`;
  try {
    const d = await (await fetch(`/api/nfl/week?week=${wk}`)).json();
    if (d.error || !(d.games && d.games.length)) {
      if (attempt < 9) { setTimeout(() => loadNFLWeek(attempt + 1), 6000); return; }
      box.innerHTML = `<div class="empty">${d.error || "No games for this week."}</div>`;
      return;
    }
    _nflWeekData = d;
    renderNFLWeek();
  } catch (e) {
    if (attempt < 9) { setTimeout(() => loadNFLWeek(attempt + 1), 6000); return; }
    box.innerHTML = `<div class="empty">NFL week board unavailable.</div>`;
  }
}
function _nflPlayers(v) {
  return (v.players || []).map((p) =>
    `<span class="nfl-pl"><span class="nfl-plrole">${p.role}</span> ${p.name} <b>${p.proj_yds}</b> yds</span>`).join("");
}
function nflGameCard(g) {
  return `<div class="bbgame nflcard">
    <div class="top"><div>
      <div class="matchup">${g.away_name} @ ${g.home_name} <span class="small" style="color:var(--muted)">${(g.date || "").slice(0, 10)}</span></div>
      <div class="pick">Model: <b>${g.fav}</b> by ${g.spread} · total ${g.exp_total}</div>
    </div></div>
    <div class="nfl-score"><span class="nfl-sc">${g.away} <b>${g.score_away}</b></span><span class="nfl-scsep">—</span><span class="nfl-sc"><b>${g.score_home}</b> ${g.home}</span></div>
    <div class="winbar"><div class="fill" style="width:${g.p_home}%"></div>
      <div class="lbl">${g.away} ${g.p_away}% — ${g.p_home}% ${g.home}</div></div>
    <div class="matchgrid">
      <div>
        <div class="teamhdr">${g.away} ${g.away_rec} · away</div>
        <div class="small">${g.away_view.pass_yds} pass · ${g.away_view.rush_yds} rush yds · <b>${g.away_view.tds}</b> TD <span style="color:var(--muted)">(${g.away_view.pass_td} pass / ${g.away_view.rush_td} rush)</span></div>
        <div class="nfl-pls">${_nflPlayers(g.away_view)}</div>
      </div>
      <div>
        <div class="teamhdr">${g.home} ${g.home_rec} · home</div>
        <div class="small">${g.home_view.pass_yds} pass · ${g.home_view.rush_yds} rush yds · <b>${g.home_view.tds}</b> TD <span style="color:var(--muted)">(${g.home_view.pass_td} pass / ${g.home_view.rush_td} rush)</span></div>
        <div class="nfl-pls">${_nflPlayers(g.home_view)}</div>
      </div>
    </div>
  </div>`;
}
function renderNFLWeek() {
  const d = _nflWeekData; if (!d) return;
  $("nflWeekSummary").innerHTML = `<b>${d.n}</b> games · Week ${d.week} · ratings from ${d.ratings_season} season. <i style="color:var(--muted)">${d.note}</i>`;
  $("nflWeekResults").innerHTML = d.games.map(nflGameCard).join("");
}

// ---- NFL Sleeper-seeded sim (Sim cards / Best ball / Pick 6) ---------------
let _nflSimData = null, _nflSimView = "sim";
function initNFLSim() {
  const sel = $("nflSimWeek");
  if (sel && !sel.dataset.filled) {
    sel.dataset.filled = "1";
    let opts = "";
    for (let w = 1; w <= 18; w++) opts += `<option value="${w}">Week ${w}</option>`;
    sel.innerHTML = opts;
    sel.addEventListener("change", () => { _nflSimData = null; $("nflSimResults").dataset.loaded = ""; loadNFLSim(0); });
  }
  if (_nflSimData) { renderNFLSim(); return; }
  if (!$("nflSimResults").dataset.loaded) { $("nflSimResults").dataset.loaded = "1"; loadNFLSim(0); }
}
async function loadNFLSim(attempt) {
  attempt = attempt || 0;
  const box = $("nflSimResults"), wk = ($("nflSimWeek") || {}).value || 1;
  if (!attempt) box.innerHTML = `<div class="empty">Simulating Week ${wk} in the background — Sleeper projections + 16 correlated game sims (~10s). Auto-refreshes.</div>`;
  try {
    const d = await (await fetch(`/api/nfl/sim?week=${wk}`)).json();
    if (d.error || !(d.games && d.games.length)) {
      if (attempt < 9) { setTimeout(() => loadNFLSim(attempt + 1), 6000); return; }
      box.innerHTML = `<div class="empty">${d.error || "No sim available."}</div>`;
      return;
    }
    _nflSimData = d;
    renderNFLSim();
  } catch (e) {
    if (attempt < 9) { setTimeout(() => loadNFLSim(attempt + 1), 6000); return; }
    box.innerHTML = `<div class="empty">NFL sim unavailable.</div>`;
  }
}
function renderNFLSim() {
  const d = _nflSimData; if (!d) return;
  $("nflSimSummary").innerHTML = `Week ${d.week} · <b>${d.n_games}</b> games · ${nf(d.n_sims)} sims each. <i style="color:var(--muted)">${d.note}</i>`;
  if (_nflSimView === "best") return renderNFLBest(d);
  if (_nflSimView === "pick6") return renderNFLPick6(d);
  renderNFLSimCards(d);
}
function renderNFLSimCards(d) {
  $("nflSimResults").innerHTML = d.games.map((g) => {
    const rows = g.players.slice(0, 12).map((p) => `<div class="nfl-simrow">
      <span class="nfl-srpos">${p.pos}</span>
      <span class="nfl-srname">${p.name} <span class="small" style="color:var(--faint)">${p.team}${p.opp ? " v " + p.opp : ""}</span></span>
      <span class="nfl-srnum">proj <b>${p.proj_pts}</b></span>
      <span class="nfl-srnum">floor ${p.floor}</span>
      <span class="nfl-srnum">ceil <b class="ev pos">${p.ceiling}</b></span>
      <span class="nfl-srnum">boom ${p.boom_pct}%</span></div>`).join("");
    return `<div class="bbgame nflcard"><div class="matchup" style="margin-bottom:6px">${g.label} <span class="small" style="color:var(--muted)">${nf(g.n_sims)} sims</span></div>${rows}</div>`;
  }).join("");
}
function renderNFLBest(d) {
  const adp = (p) => p.adp != null ? `<span class="small" style="color:var(--faint)" title="our draft-board rank (model value blended toward Sleeper consensus)">#${p.adp}</span>` : "";
  const rows = d.ceilings.slice(0, 40).map((p, i) => `<div class="futrow nflbestrow">
      <span class="fr-rank">${i + 1}</span>
      <span class="fr-team">${p.pos} ${p.name} <span class="small" style="color:var(--muted)">${p.team} · ${p.matchup}</span> ${adp(p)}</span>
      <span class="fr-num">${p.proj_pts}</span>
      <span class="fr-num"><b class="ev pos">${p.ceiling}</b></span>
      <span class="fr-num">${p.boom_pct}%</span></div>`).join("");
  const stacks = (d.stacks || []).slice(0, 8).map((s) => `<div class="nfl-stack">
      <b>${s.team}</b> ${s.qb} <span style="color:var(--faint)">+</span> ${s.receivers.join(" + ")}
      <span class="nfl-stnum">combined ceiling <b class="ev pos">${s.combined_ceiling}</b></span>
      <span class="small" style="color:var(--muted)">QB↔WR corr ${s.qb_wr_corr}</span></div>`).join("");
  $("nflSimResults").innerHTML =
    `<div class="small" style="margin:2px 0 6px"><b>🥇 Ceiling board</b> — best-ball values the 90th-percentile game. ${d.has_adp ? "ADP = your Sleeper consensus rank." : ""}</div>
     <div class="futrow nflbestrow rchead"><span class="fr-rank">#</span><span class="fr-team">Player</span><span class="fr-num">proj</span><span class="fr-num">ceil</span><span class="fr-num">boom</span></div>
     ${rows}
     <div class="small" style="margin:14px 0 6px"><b>🔗 Top stacks</b> — a QB + his two best pass-catchers, combined 90th-percentile ceiling (the correlation is why stacking wins best ball).</div>
     ${stacks}`;
}
function renderNFLPick6(d) {
  if (!d.props || !d.props.length) { $("nflSimResults").innerHTML = `<div class="empty">No prop leans this week.</div>`; return; }
  const rows = d.props.slice(0, 50).map((p) => `<div class="p6row p6row-nfl" style="cursor:default">
      <span class="p6side ${p.side === "More" ? "more" : "less"}">${p.side} ${p.line}</span>
      <span class="p6name">${p.player} <span class="p6stat">${p.stat} · ${p.team}</span></span>
      <span class="p6game">${p.matchup}</span>
      <span class="p6prob"><b>${p.prob}%</b><span class="p6proj">proj ${p.proj}</span></span></div>`).join("");
  $("nflSimResults").innerHTML =
    `<div class="small" style="margin:2px 0 8px">Correlation-aware prop More/Less from the game sim (yards &amp; receptions). DK &amp; PrizePicks set the line — take ours where it clears theirs. Same-game legs are correlated — the sim already knows.</div>
     <div class="p6list">${rows}</div>`;
}
async function loadNFL() {
  try {
    const r = await fetch("/api/pro/nfl");
    if (r.status === 202) {       // projection warming up — poll until ready
      $("nflResults").innerHTML = `<div class="empty">Projecting the season (4,000 simulations)… this takes ~30s on a cold start.</div>`;
      if (!_nflPoll) _nflPoll = setInterval(loadNFL, 8000);
      return;
    }
    const d = await r.json();
    if (d.error) { $("nflResults").innerHTML = `<div class="empty">${d.error}</div>`; return; }
    if (_nflPoll) { clearInterval(_nflPoll); _nflPoll = null; }
    _nflData = d;
    renderNFL();
  } catch (e) { $("nflResults").innerHTML = `<div class="empty">Failed to load.</div>`; }
}
function _nflOut(t) {  // small note of who's out / rookies driving the roster modifier
  const outs = (t.out_list || []).filter((o) => o.reason === "suspended" || o.reason === "injured");
  if (!outs.length) return "";
  return ` <span class="small" style="color:var(--neg)" title="${outs.map((o) => o.pos + " " + o.name + " (" + o.reason + ")").join(", ")}">▾${outs.length} out</span>`;
}
function _nflQB(t) {
  const q = t.qb;
  if (!q || !q.starter) return "";
  const cls = q.adj >= 0.8 ? "ev pos" : q.adj <= -0.8 ? "ev neg" : "";
  const tag = q.unproven ? " (unproven)" : q.changed ? " (new)" : "";
  const tip = `Projected starter: ${q.starter}${tag} — last-season efficiency ${q.score} vs league ${q.lg_avg}` +
    (q.changed ? `; replaces a ${q.prev_score} passer` : "") +
    `. QB rating adjustment ${q.adj >= 0 ? "+" : ""}${q.adj} pts/gm.`;
  return `<span class="small" style="display:block;color:var(--muted)" title="${tip}">QB ${q.starter}${tag}${q.adj ? ` <b class="${cls}">${q.adj >= 0 ? "+" : ""}${q.adj}</b>` : ""}</span>`;
}
function renderNFL() {
  const d = _nflData; if (!d) return;
  const age = d.age_sec != null ? `updated ${agoStr(d.age_sec)} · ` : "";
  $("nflSummary").innerHTML = `Simulated <b>${(d.n_sims || 0).toLocaleString()}</b> seasons · prior-year point differential (regressed) + <b>projected-starter QB layer</b> (efficiency + QB-change swings) + live roster availability from ESPN. <span style="color:var(--muted)">${age}refreshes nightly. 🏆 = franchise Super Bowl titles ${d.titles_asof || ""} — shown for context, never used in the model (a 1975 ring predicts nothing).</span> <button class="track-mini" onclick="rerunPro('nfl')">↻ rerun</button>`;
  if (_nflSub === "wins") return renderNFLWins(d);
  const rows = d.teams.map((t, i) => `<div class="futrow nflrow">
      <span class="fr-rank">${i + 1}</span>
      <span class="fr-team">${t.name}${t.titles ? ` <span class="small" title="franchise Super Bowl titles ${d.titles_asof || ""} (display only)">🏆${t.titles}</span>` : ""} <span class="small" style="color:var(--muted)">${(t.division || "").replace(/^(AFC|NFC) /, "$1 ")}</span>${_nflOut(t)}${_nflQB(t)}</span>
      <span class="fr-num">${t.champ_pct}%</span>
      <span class="fr-num">${t.conf_pct}%</span>
      <span class="fr-num">${t.division_pct}%</span>
      <span class="fr-num">${t.playoff_pct}%</span>
      <span class="fr-num">${t.proj_wins}</span>
      <span class="fr-num small" style="color:var(--muted)">${t.prior}</span></div>`).join("");
  $("nflResults").innerHTML = `
    <div class="futrow nflrow rchead"><span class="fr-rank">#</span><span class="fr-team">Team</span>
      <span class="fr-num">SB</span><span class="fr-num">Conf</span><span class="fr-num">Div</span>
      <span class="fr-num">Playoff</span><span class="fr-num">Proj W</span><span class="fr-num">'25</span></div>
    ${rows}
    <div class="small" style="color:var(--muted);margin-top:8px">Super Bowl / conference / division markets light up here once Kalshi opens them. Win-total edges are live now — see the Win totals tab.</div>`;
}
function renderNFLWins(d) {
  // Teams with win-total markets, sorted by the biggest available edge.
  const teams = d.teams.filter((t) => (t.markets || {}).win_totals && t.markets.win_totals.length);
  if (!teams.length) { $("nflResults").innerHTML = `<div class="empty">No win-total markets matched right now.</div>`; return; }
  const rows = teams.map((t) => {
    const cells = t.markets.win_totals.map((w) => {
      const ec = w.edge == null ? "" : (w.edge >= 0 ? "pos" : "neg");
      return `<span class="nflwt ${ec}" title="model ${w.model}% vs ${w.cents}¢">${w.line}+ <b>${w.edge >= 0 ? "+" : ""}${w.edge == null ? "—" : w.edge}</b></span>`;
    }).join("");
    return `<div class="nflwtrow"><div class="nflwt-team"><b>${t.name}</b> <span class="small" style="color:var(--muted)">proj ${t.proj_wins}W</span></div><div class="nflwt-lines">${cells}</div></div>`;
  });
  $("nflResults").innerHTML = `<div class="small" style="color:var(--muted);margin-bottom:6px">Each chip = win-total line: <b>edge</b> (model% − Kalshi ¢). Green = model likes the over.</div>${rows.join("")}`;
}
async function rerunPro(lg) {
  try { await fetch(`/api/sim/rerun?sport=${lg}`, { method: "POST" }); } catch (e) {}
  $("nflResults").innerHTML = `<div class="empty">Rerun started — reloading shortly…</div>`;
  setTimeout(loadNFL, 6000);
}

// ---- Best-Ball Draft Room ----
let _drPool = null, _drState = null, _drPoll = null, _drPos = "ALL";
function initDraft() {
  if (!$("draftResults").dataset.loaded) { $("draftResults").dataset.loaded = "1"; loadDraft(); }
}
async function loadDraft() {
  try {
    const r = await fetch("/api/nfl/fantasy");
    if (r.status === 202) {
      $("draftResults").innerHTML = `<div class="empty">Simulating the season for every player (4,000 seasons w/ injury, variance, boom/bust)… ~1 min cold start.</div>`;
      if (!_drPoll) _drPoll = setInterval(loadDraft, 8000);
      return;
    }
    const d = await r.json();
    if (d.error) { $("draftResults").innerHTML = `<div class="empty">${d.error}</div>`; return; }
    if (_drPoll) { clearInterval(_drPoll); _drPoll = null; }
    _drPool = d.pool;
    $("draftResults").innerHTML = "";
    renderDraftSetup();
  } catch (e) { $("draftResults").innerHTML = `<div class="empty">Failed to load.</div>`; }
}
function renderDraftSetup() {
  const slots = Array.from({ length: 12 }, (_, i) => i + 1)
    .map((s) => `<button class="dr-slot" onclick="startDraft(${s})">${s}</button>`).join("");
  $("draftSetup").innerHTML = `<div class="small">12-team snake best-ball · pick your draft slot:</div>
    <div class="dr-slots">${slots}</div>
    <div class="small" style="color:var(--muted);margin-top:4px">${_drPool.length} players projected (vets + rookies). I'll log every team's pick as you go and tell you who to take on your clock.</div>`;
  $("draftSummary").innerHTML = "";
}
// snake order: which team (0-indexed) is on the clock for a 0-indexed overall pick
function _drTeam(p, teams) {
  const rd = Math.floor(p / teams), inr = p % teams;
  return rd % 2 === 0 ? inr : teams - 1 - inr;
}
function startDraft(slot) {
  const teams = 12, rounds = 18;
  _drState = {
    teams, rounds, mySlot: slot - 1, pick: 0,
    drafted: {}, rosters: Array.from({ length: teams }, () => []),
  };
  $("draftSetup").innerHTML = `<button class="track-mini" onclick="renderDraftSetup()">↺ restart</button>
    <span class="small" style="margin-left:8px">You are <b>Team ${slot}</b> (12-team snake, ${rounds} rounds).</span>`;
  $("draftBody").classList.remove("hidden");
  drawDraft();
}
function _drMyNextPick() {  // 0-indexed overall pick number of my next turn (or -1)
  const s = _drState;
  for (let p = s.pick; p < s.teams * s.rounds; p++) if (_drTeam(p, s.teams) === s.mySlot) return p;
  return -1;
}
function draftPlayer(id) {
  const s = _drState; if (!s) return;
  const team = _drTeam(s.pick, s.teams);
  s.drafted[id] = team;
  const pl = _drPool.find((p) => p.id === id);
  if (pl) s.rosters[team].push(pl);
  s.pick++;
  drawDraft();
}
function _drAvail() { return _drPool.filter((p) => !(p.id in _drState.drafted)); }
function drawDraft() {
  const s = _drState;
  const done = s.pick >= s.teams * s.rounds;
  const team = done ? -1 : _drTeam(s.pick, s.teams);
  const myTurn = team === s.mySlot;
  const rd = Math.floor(s.pick / s.teams) + 1, inr = (s.pick % s.teams) + 1;
  $("draftOnClock").innerHTML = done ? "Draft complete 🏆"
    : myTurn ? `<b style="color:var(--pos)">YOUR PICK — ${rd}.${String(inr).padStart(2, "0")}</b>`
      : `On the clock: <b>Team ${team + 1}</b> · pick ${rd}.${String(inr).padStart(2, "0")}`;
  renderDraftRecs(myTurn);
  renderDraftPool();
  renderDraftRoster();
  renderDraftBoardGrid();
}
function _drStackBonus(pl, roster) {
  // best-ball loves correlation: QB with his pass-catchers and vice-versa
  if (pl.pos === "QB") {
    if (roster.some((r) => (r.pos === "WR" || r.pos === "TE") && r.team === pl.team)) return 1;
  } else if (pl.pos === "WR" || pl.pos === "TE") {
    if (roster.some((r) => r.pos === "QB" && r.team === pl.team)) return 1;
  }
  return 0;
}
function _drNeed(pos, roster, rounds) {
  const target = { QB: Math.max(2, rounds * 0.12), RB: rounds * 0.33, WR: rounds * 0.42, TE: Math.max(2, rounds * 0.12) }[pos] || 1;
  const have = roster.filter((r) => r.pos === pos).length;
  return Math.max(0.55, Math.min(1.5, 1 + (target - have) / target * 0.6));
}
function computeRecs() {
  const s = _drState, avail = _drAvail(), roster = s.rosters[s.mySlot];
  const myNext = _drMyNextPick();
  const after = myNext < 0 ? 0 : (() => { for (let p = myNext + 1; p < s.teams * s.rounds; p++) if (_drTeam(p, s.teams) === s.mySlot) return p; return s.teams * s.rounds; })();
  const gap = after - myNext;                       // picks between this and my next turn
  const scored = avail.map((pl) => {
    const stack = _drStackBonus(pl, roster);
    const need = _drNeed(pl.pos, roster, s.rounds);
    const score = (pl.value + 12) * need * (1 + 0.18 * stack);
    return { pl, score, stack, need };
  }).sort((a, b) => b.score - a.score);
  // rank within available by raw VOR for the "won't last" call
  const byVor = [...avail].sort((a, b) => b.value - a.value);
  return scored.slice(0, 4).map((x) => {
    const reasons = [];
    if (byVor[0] && byVor[0].id === x.pl.id) reasons.push("Best value on the board");
    if (x.stack) reasons.push(`Stacks your ${x.pl.pos === "QB" ? "pass-catcher" : "QB"} (${x.pl.team})`);
    if (x.need >= 1.2) reasons.push(`You're light at ${x.pl.pos}`);
    else if (x.need <= 0.7) reasons.push(`Already deep at ${x.pl.pos}`);
    const vorRank = byVor.findIndex((p) => p.id === x.pl.id);
    if (gap > 0 && vorRank >= 0 && vorRank < gap * 0.8) reasons.push(`Won't last to your next pick`);
    if (x.pl.rookie) reasons.push("Rookie upside (boom/bust)");
    if (x.pl.injury_return) reasons.push("Injury bounce-back (consensus still buys him)");
    if (x.pl.fa) reasons.push("Free agent — landing spot pending");
    else if (x.pl.new_team) reasons.push(`New team (${x.pl.sleeper_team || x.pl.team})`);
    return { ...x.pl, reasons };
  });
}
function _drFlags(p) {
  // Forward-looking status badges from the live draft consensus / injury feed.
  let h = "";
  if (p.fa) h += ' <span class="dr-fl fa" title="Free agent / unsigned">FA</span>';
  if (p.new_team) h += ` <span class="dr-fl new" title="New team this season">→${p.sleeper_team || ""}</span>`;
  if (p.injury_return) h += ` <span class="dr-fl ir" title="Returning from injury">${p.injury || "INJ"}</span>`;
  else if (p.injury) h += ` <span class="dr-fl q" title="${p.injury}">Q</span>`;
  return h;
}
function _drCons(p) {
  return p.consensus_rank ? ` <span class="dr-cons" title="Sleeper consensus draft rank">ADP ${p.consensus_rank}</span>` : "";
}
function renderDraftRecs(myTurn) {
  if (!myTurn) { $("draftRecs").innerHTML = ""; return; }
  const recs = computeRecs();
  if (!recs.length) { $("draftRecs").innerHTML = ""; return; }
  $("draftRecs").innerHTML = `<div class="dr-recs"><div class="dr-recs-h">🎯 Take one of these:</div>
    ${recs.map((r, i) => `<div class="dr-rec ${i === 0 ? "top" : ""}">
      <div class="dr-rec-main"><b>${r.name}</b> <span class="legtag">${r.pos}</span> <span class="dr-team">${r.team}</span>${r.rookie ? ' <span class="dr-rk">R</span>' : ""}${_drFlags(r)}${_drCons(r)}</div>
      <div class="dr-rec-meta">VOR <b>${r.value}</b> · boom ${r.boom} · ${r.reasons.join(" · ")}</div>
      <button class="track-mini primary-mini" onclick="draftPlayer('${r.id}')">Draft</button></div>`).join("")}</div>`;
}
function renderDraftPool() {
  if (!_drState) return;
  const q = ($("draftSearch") ? $("draftSearch").value : "").toLowerCase();
  const filt = ["ALL", "QB", "RB", "WR", "TE"].map((p) =>
    `<button class="dr-pf ${_drPos === p ? "active" : ""}" onclick="_drPos='${p}';renderDraftPool()">${p}</button>`).join("");
  $("draftPosFilter").innerHTML = filt;
  let avail = _drAvail();
  if (_drPos !== "ALL") avail = avail.filter((p) => p.pos === _drPos);
  if (q) avail = avail.filter((p) => (p.name || "").toLowerCase().includes(q));
  const rows = avail.slice(0, 80).map((p) => `<div class="dr-prow" onclick="draftPlayer('${p.id}')">
    <span class="dr-padp" title="our board rank — the pool is sorted by blended value">${p.adp}</span>
    <span class="dr-pname">${p.name}${p.rookie ? ' <span class="dr-rk">R</span>' : ""}${_drFlags(p)}${_drCons(p)}</span>
    <span class="legtag">${p.pos}</span><span class="dr-team">${p.team || ""}</span>
    <span class="dr-pvor">VOR ${p.value}</span><span class="dr-pboom">${p.boom}</span></div>`).join("");
  $("draftPool").innerHTML = rows || `<div class="empty">No players.</div>`;
}
function renderDraftRoster() {
  const r = _drState.rosters[_drState.mySlot];
  const cnt = { QB: 0, RB: 0, WR: 0, TE: 0 };
  r.forEach((p) => cnt[p.pos]++);
  $("draftRoster").innerHTML = `<div class="dr-roster-h">Your roster (${r.length}) · QB ${cnt.QB} RB ${cnt.RB} WR ${cnt.WR} TE ${cnt.TE}</div>
    ${r.map((p) => `<div class="dr-rrow"><span class="legtag">${p.pos}</span> ${p.name} <span class="dr-team">${p.team}</span>${_drFlags(p)}</div>`).join("") || '<div class="small" style="color:var(--muted)">no picks yet</div>'}`;
}
function renderDraftBoardGrid() {
  const s = _drState, team = s.pick < s.teams * s.rounds ? _drTeam(s.pick, s.teams) : -1;
  $("draftBoard").innerHTML = `<div class="dr-board-h">Teams (picks made)</div><div class="dr-teams">` +
    s.rosters.map((ro, i) => `<span class="dr-tcount ${i === s.mySlot ? "mine" : ""} ${i === team ? "clock" : ""}">T${i + 1}:${ro.length}</span>`).join("") + "</div>";
}

// ---- AI key setup (optional, stored locally on the user's machine) ----
async function refreshAiKeyStatus() {
  try {
    const d = await (await fetch("/api/settings/ai_key")).json();
    _setAiKeyUI(d.configured);
  } catch (e) { /* leave as-is */ }
}
function _setAiKeyUI(configured) {
  const s = $("aiKeyStatus");
  if (s) s.innerHTML = configured ? '<span style="color:var(--pos)">✓ on</span>' : '<span style="color:var(--muted)">(off)</span>';
  if (configured && $("gradeLLM")) $("gradeLLM").checked = true;
}
async function saveAiKey(key) {
  const msg = $("aiKeyMsg");
  msg.textContent = key ? "Verifying…" : "Clearing…";
  try {
    const d = await (await fetch("/api/settings/ai_key", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key }) })).json();
    if (d.error) { msg.innerHTML = `<span style="color:#e0566a">${d.error}</span>`; return; }
    if (d.cleared) { msg.innerHTML = '<span style="color:var(--muted)">Key removed — AI explanations off.</span>'; _setAiKeyUI(false); $("aiKeyInput").value = ""; return; }
    msg.innerHTML = '<span style="color:var(--pos)">✓ Key verified &amp; saved (stored locally). AI explanations are on.</span>';
    _setAiKeyUI(true);
    $("aiKeyInput").value = "";
  } catch (e) { msg.innerHTML = '<span style="color:#e0566a">Save failed — try again.</span>'; }
}

// ---- Best-ball Team Grader (multi-team) ----
let _gradeTeams = [{ label: "", text: "", pitch: "" }];
function renderGradeTeams() {
  $("gradeTeams").innerHTML = _gradeTeams.map((t, i) => `
    <div class="grade-team-in">
      <div class="grade-team-hd">
        <input class="grade-label" placeholder="Title / drafter name (e.g. Grok, ChatGPT, You, Dad)" value="${(t.label || "").replace(/"/g, "&quot;")}" oninput="_gradeTeams[${i}].label=this.value">
        ${_gradeTeams.length > 1 ? `<button class="track-mini" onclick="removeGradeTeam(${i})">✕</button>` : ""}
      </div>
      <textarea class="grade-ta" rows="5" placeholder="Paste this team — one player per line, or comma-separated&#10;Josh Allen&#10;Bijan Robinson&#10;…" oninput="_gradeTeams[${i}].text=this.value">${t.text || ""}</textarea>
      <textarea class="grade-ta grade-pitch" rows="2" placeholder="Their argument (optional) — let this drafter plead their case to sway the AI grader…" oninput="_gradeTeams[${i}].pitch=this.value">${t.pitch || ""}</textarea>
    </div>`).join("");
}
window.removeGradeTeam = (i) => { _gradeTeams.splice(i, 1); if (!_gradeTeams.length) _gradeTeams.push({ label: "", text: "", pitch: "" }); renderGradeTeams(); };
function addGradeTeam(label, text) { _gradeTeams.push({ label: label || "", text: text || "", pitch: "" }); renderGradeTeams(); }
function gradeAddMine() {
  if (!_drState || !_drState.rosters[_drState.mySlot] || !_drState.rosters[_drState.mySlot].length) {
    $("gradeOut").innerHTML = `<div class="empty">No drafted team yet — start a draft and make some picks first.</div>`;
    return;
  }
  addGradeTeam("My team", _drState.rosters[_drState.mySlot].map((p) => p.name).join("\n"));
}
async function gradeRunAll() {
  const out = $("gradeOut");
  const teams = _gradeTeams
    .map((t) => ({ label: t.label, pitch: t.pitch, names: (t.text || "").split(/[\n,]+/).map((s) => s.trim()).filter(Boolean) }))
    .filter((t) => t.names.length);
  if (!teams.length) { out.innerHTML = `<div class="empty">Paste at least one team.</div>`; return; }
  saveGradeTeams(true);                       // persist so a re-run needs no retyping
  const useLLM = $("gradeLLM") && $("gradeLLM").checked;
  out.innerHTML = `<div class="empty">Grading ${teams.length} team${teams.length > 1 ? "s" : ""}…${useLLM ? " (AI explanations on)" : ""}</div>`;
  try {
    const r = await fetch("/api/nfl/grade", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ teams, use_llm: useLLM }) });
    if (r.status === 202) { out.innerHTML = `<div class="empty">Warming the projection pool (cold start, ~1 min) — try again shortly.</div>`; return; }
    const d = await r.json();
    if (d.error) { out.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    out.innerHTML = renderMultiGrade(d);
  } catch (e) { out.innerHTML = `<div class="empty">Grade failed — try again.</div>`; }
}

async function saveGradeTeams(silent) {
  const teams = _gradeTeams.map((t) => ({
    label: t.label || "",
    names: (t.text || "").split(/[\n,]+/).map((s) => s.trim()).filter(Boolean),
    pitch: t.pitch || "",
  })).filter((t) => t.names.length || t.label);
  try {
    await fetch("/api/nfl/teams", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ teams }) });
    const b = $("gradeSaveBtn");
    if (b && !silent) { const o = b.textContent; b.textContent = "✓ Saved"; setTimeout(() => { b.textContent = o; }, 1500); }
  } catch (e) { /* best-effort */ }
}

async function loadGradeTeams() {
  try {
    const d = await (await fetch("/api/nfl/teams")).json();
    if (d.teams && d.teams.length) {
      _gradeTeams = d.teams.map((t) => ({ label: t.label || "", text: (t.names || []).join("\n"), pitch: t.pitch || "" }));
    }
  } catch (e) { /* keep the default blank team */ }
  renderGradeTeams();
}
function _gradeColor(letter) {
  const c = (letter || "")[0];
  return c === "A" ? "#34c77b" : c === "B" ? "#8ad06a" : c === "C" ? "#ffcf66" : c === "D" ? "#ff9f43" : "#e0566a";
}
function renderMultiGrade(d) {
  const teams = d.teams || [];
  if (!teams.length) return `<div class="empty">No teams could be graded.</div>`;
  const medal = (r) => (r === 1 ? "🥇" : r === 2 ? "🥈" : r === 3 ? "🥉" : `#${r}`);
  let html = "";
  // Leaderboard only for head-to-head (2+ teams); a single team just gets graded.
  if (teams.length > 1) {
    html += `<div class="grade-board"><div class="grade-board-h">🏆 Leaderboard</div>`;
    html += teams.map((g) => {
      const col = _gradeColor(g.grade);
      return `<div class="grade-row">
          <span class="grade-rank">${medal(g.rank)}</span>
          <span class="grade-rname"><b>${g.label}</b></span>
          <span class="grade-rgrade" style="color:${col}">${g.grade}</span>
          <span class="grade-rscore">${g.score}</span>
          <span class="small" style="color:var(--muted)">${g.archetype} · ${g.ceiling_week} ceiling</span>
        </div>`;
    }).join("");
    html += `</div>`;
  }
  // per-team detail (winner expanded)
  html += teams.map((g, i) => {
    const col = _gradeColor(g.grade);
    const cats = (g.categories || []).map((c) =>
      `<div class="grade-cat"><span class="grade-cat-l">${c.emoji} ${c.label}</span><span class="grade-cat-g" style="color:${_gradeColor(c.grade)}">${c.grade}</span><div class="grade-cat-why small">${c.why}</div></div>`).join("");
    const posCards = ["QB", "RB", "WR", "TE"].map((pos) => {
      const v = g.positions[pos]; if (!v) return "";
      return `<div class="grade-pos">
          <div class="grade-pos-h"><b>${pos}</b> <span style="color:${_gradeColor(v.grade)};font-weight:700">${v.grade}</span> <span class="small" style="color:var(--muted)">${v.count}</span></div>
          <div class="small" style="color:var(--muted)">${v.note}</div>
        </div>`;
    }).join("");
    const stacks = (g.stacks || []).length ? `🔗 ${g.stacks.map((s) => `${s.qb}+${s.partners.join(", ")}`).join(" · ")}` : "No stacks";
    const sch = g.schedule || {};
    const schBits = [];
    if (sch.playoff_sos != null) schBits.push(`Playoff (wk 15-17) SoS <b>${sch.playoff_sos}</b> <span class="small" style="color:var(--muted)">(opp wins; lower = easier)</span>`);
    (sch.matchup_notes || []).forEach((n) => schBits.push(n));
    (sch.bye_notes || []).forEach((n) => schBits.push(`📅 ${n}`));
    const schedBlock = schBits.length
      ? `<details style="margin-top:6px"><summary class="small">🗓️ Bye weeks & playoff matchups</summary><ul class="grade-list">${schBits.map((s) => `<li>${s}</li>`).join("")}</ul></details>`
      : "";
    const narrative = g.llm_narrative || g.narrative || "";
    const pitch = g.pitch ? `<div class="grade-pitch-show small">🗣️ <b>${g.label}'s case:</b> ${g.pitch}</div>` : "";
    const unmatched = (g.unmatched && g.unmatched.length)
      ? `<div class="small" style="color:#ff9f43;margin-top:6px">⚠️ Couldn't find: ${g.unmatched.join(", ")}</div>` : "";
    return `<details class="grade-card" ${i === 0 ? "open" : ""}>
      <summary class="grade-sum">
        <span class="grade-letter sm" style="color:${col};border-color:${col}">${g.grade}</span>
        <span><b>${g.label}</b> · ${g.score}/100 · ${g.archetype}</span>
      </summary>
      ${pitch}
      <div class="grade-narr">${g.llm_narrative ? "🤖 " : ""}${narrative}</div>
      <div class="grade-catgrid">${cats}</div>
      <div class="grade-posgrid">${posCards}</div>
      <div class="small" style="margin-top:8px">${stacks}</div>
      ${schedBlock}
      <div class="grade-cols">
        ${(g.strengths || []).length ? `<div><div class="grade-h">💪 Strengths</div><ul class="grade-list">${g.strengths.map((s) => `<li>${s}</li>`).join("")}</ul></div>` : ""}
        ${(g.weaknesses || []).length ? `<div><div class="grade-h">⚠️ Weaknesses</div><ul class="grade-list">${g.weaknesses.map((s) => `<li>${s}</li>`).join("")}</ul></div>` : ""}
      </div>
      <details style="margin-top:6px"><summary class="small">Best lineup</summary><div class="grade-starters">${(g.starters || []).map((s) => `<span class="grade-st"><b>${s.pos}</b> ${s.name}${s.bye ? ` <span class="grade-bye">bye ${s.bye}</span>` : ""}</span>`).join("")}</div></details>
      ${unmatched}
    </details>`;
  }).join("");
  return html;
}

// ---- UFC ----
let _ufcData = null, _ufcPoll = null;
function initUFC() {
  if (!$("ufcResults").dataset.loaded) { $("ufcResults").dataset.loaded = "1"; loadUFC(); }
}
async function loadUFC() {
  try {
    const r = await fetch("/api/ufc");
    if (r.status === 202) {
      $("ufcResults").innerHTML = `<div class="empty">Rating every fighter from their fight history & simulating the card… ~1 min on a cold start.</div>`;
      if (!_ufcPoll) _ufcPoll = setInterval(loadUFC, 8000);
      return;
    }
    const d = await r.json();
    if (d.error) { $("ufcResults").innerHTML = `<div class="empty">${d.error}</div>`; return; }
    if (_ufcPoll) { clearInterval(_ufcPoll); _ufcPoll = null; }
    _ufcData = d;
    renderUFC();
  } catch (e) { $("ufcResults").innerHTML = `<div class="empty">Failed to load.</div>`; }
}
function _ufcEdge(e) {
  if (e == null) return "—";
  return `<span class="ev ${e >= 0 ? "pos" : "neg"}">${e >= 0 ? "+" : ""}${e}</span>`;
}
function _ratingChip(f) {
  if (f.rating == null) return "";
  const rc = f.rating >= 60 ? "#3ad17a" : f.rating <= 40 ? "#e0566a" : "var(--muted)";
  const c = f.components || {};
  const tip = [`Power rating (league avg = 50) from our fight-history model — not an Elo.`,
    `Striking ${c.striking}`, `Strike def ${c.str_def}`, `Power/KD ${c.power}`,
    `Finishing ${c.finishing}`, `Takedowns ${c.takedowns}`, `TD def ${c.td_def}`,
    `Durability ${c.durability}`].join(" • ");
  let flag = "";
  if (f.debut) flag = ` <span class="ufc-debut" title="UFC debut — no Octagon box scores yet. Striking/grappling are league-average estimates; finishing & durability are seeded from the pro record (regional competition is softer than the UFC, so it's shrunk).">⚠️ UFC debut · pro ${f.career_record}</span>`;
  else if (f.defaulted) flag = ` <span class="ufc-debut" title="No fight history at all — a league-average placeholder (rating 50), not a real read">⚠️ no data</span>`;
  else if (f.thin) flag = ` <span class="ufc-debut" title="Few UFC fights — rating shrunk hard toward league average, treat with caution${f.career_record ? "; pro record " + f.career_record : ""}">⚠️ thin${f.career_record ? " · pro " + f.career_record : ""}</span>`;
  return ` <span class="ufc-rating" style="border-color:${rc};color:${rc}" title="${tip}">⚡${f.rating}</span>${flag}`;
}
function _fighterRow(f) {
  const px = f.kalshi_cents != null ? `${f.kalshi_cents}¢` : "—";
  const fair = f.fair_win != null && f.fair_win !== f.win_pct
    ? ` <span class="small" style="color:var(--muted)" title="confidence-blended with the market">→${f.fair_win}%</span>` : "";
  const recTxt = f.fights > 0
    ? `${f.record} · ${f.fights}f`
    : (f.career_record ? `pro ${f.career_record}` : `${f.record} · ${f.fights}f`);
  return `<div class="ufc-fighter">
      <div class="ufc-fname"><b>${f.name}</b>${_ratingChip(f)} <span class="small" style="color:var(--muted)">${recTxt}</span></div>
      <div class="ufc-fnums"><span class="ufc-win">${f.win_pct}%${fair}</span>
        <span class="fr-num">${px}</span><span class="fr-num">${_ufcEdge(f.edge)}</span>
        <span class="fr-num" title="DraftKings projection / ceiling">DK ${f.proj}<span style="color:var(--muted)">/${f.ceil}</span></span></div>
    </div>`;
}
function renderUFC() {
  const d = _ufcData; if (!d) return;
  $("ufcSummary").innerHTML = `<b>${d.event || "Upcoming card"}</b>${d.date ? " · " + d.date : ""} · ${d.bouts.length} bouts · model = ratings from each fighter's past fights → win prob, method/round & DK points. <span class="ufc-rating" style="border-color:var(--muted);color:var(--muted)">⚡</span> = our 0-100 power rating (league avg 50; hover for the striking/grappling/finishing breakdown). ⚠️ flags fighters with no/thin UFC history running on a league-average baseline. Edge = our fair win% (blended toward the market when history is thin) − Kalshi ask.`;
  const bouts = d.bouts.map((bt) => {
    const m = bt.method || {};
    const methodBar = `<div class="ufc-method">
        <span title="KO/TKO">KO ${m.ko ?? 0}%</span><span title="Submission">SUB ${m.sub ?? 0}%</span><span title="Decision">DEC ${m.dec ?? 0}%</span></div>`;
    return `<div class="ufc-bout">
        <div class="ufc-bweight">${bt.weight || ""} · ${bt.rounds || 3} rounds</div>
        ${_fighterRow(bt.a)}${_fighterRow(bt.b)}
        ${methodBar}
      </div>`;
  }).join("");
  $("ufcResults").innerHTML = bouts;
}

// ---- Tennis ----
let _tnData = null, _tnLivePoll = null, _tnPoll = null, _tnSub = "all";
function initTennis() {
  if (!$("tnResults").dataset.loaded) {
    $("tnResults").dataset.loaded = "1";
    loadTennis();
    $("tnSearch")?.addEventListener("input", () => renderTennis());
    document.querySelectorAll("#tnSubtabs .subtab").forEach((b) => {
      b.addEventListener("click", () => {
        document.querySelectorAll("#tnSubtabs .subtab").forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
        _tnSub = b.dataset.tnsub;
        renderTennis();
        clearInterval(_tnLivePoll); _tnLivePoll = null;
        if (_tnSub === "live" || _tnSub === "upsets") _tnLivePoll = setInterval(loadTennis, 60000);
      });
    });
  }
}
async function loadTennis() {
  try {
    const r = await fetch("/api/tennis");
    if (r.status === 202) {
      $("tnResults").innerHTML = `<div class="empty">Rating every player from their charted matches & simulating each match point-by-point… ~1 min on a cold start.</div>`;
      if (!_tnPoll) _tnPoll = setInterval(loadTennis, 8000);
      return;
    }
    const d = await r.json();
    if (d.error) { $("tnResults").innerHTML = `<div class="empty">${d.error}</div>`; return; }
    if (_tnPoll) { clearInterval(_tnPoll); _tnPoll = null; }
    _tnData = d;
    renderTennis();
  } catch (e) { $("tnResults").innerHTML = `<div class="empty">Failed to load.</div>`; }
}
function _tnEdge(e) {
  if (e == null) return "";
  return `<span class="ev ${e >= 0 ? "pos" : "neg"}">${e >= 0 ? "+" : ""}${e}</span>`;
}
function _tnPlayer(p, served) {
  const px = p.cents != null ? `${p.cents}¢` : "—";
  // HEADLINE = fair_win: the confidence-blended number we actually believe and
  // that the Lean uses. (fair_win already equals the market when we have no
  // model, or the model when there's no market.) Leading with the raw serve
  // sim was the bug — on thin data it can wildly favor the wrong player.
  const believe = p.fair_win != null ? p.fair_win
    : (p.model_win != null ? p.model_win : p.mkt_win);
  const mktTag = (p.model_win == null && p.mkt_win != null)
    ? `<span class="small" style="color:var(--muted)"> mkt</span>` : "";
  const headline = believe != null ? `${believe}%${mktTag}` : "—";
  // Secondary: the raw serve-model read, shown ONLY when it meaningfully
  // differs from the fair number (i.e. thin data pulled us to the market), so
  // the number and the story never silently contradict each other.
  const raw = (p.model_win != null && p.fair_win != null
    && Math.abs(p.model_win - p.fair_win) > 3)
    ? ` <span class="small" style="color:var(--faint)" title="raw serve-model read before blending toward the market — differs when charting is thin, so we trust the blended number above">serve ${p.model_win}%</span>` : "";
  // Variance reality check on a strong favorite (from the number we believe).
  const risk = (believe != null && believe >= 70 && believe < 100)
    ? ` <span class="tn-risk" title="even a heavy favorite loses this often — single tennis matches are high-variance">1 in ${Math.round(100 / (100 - believe))} loses</span>` : "";
  const hold = p.hold != null ? `<span class="tn-hold" title="probability of holding serve">hold ${p.hold}%</span>` : "";
  const elo = p.elo != null ? `<span class="tn-hold" title="our Elo rating from recent match results (${p.elo_n} matches)">Elo ${p.elo}</span>` : "";
  return `<div class="tn-player">
      <div class="tn-pname"><b>${p.name}</b> ${hold}${elo}${risk}</div>
      <div class="tn-pnums"><span class="tn-win">${headline}${raw}</span>
        <span class="fr-num">${px}</span><span class="fr-num">${_tnEdge(p.edge)}</span></div>
    </div>`;
}
function renderTennis() {
  const d = _tnData; if (!d) return;
  let matches = (d.matches || []).slice();
  if (_tnSub === "atp") matches = matches.filter((m) => m.tour === "ATP");
  else if (_tnSub === "wta") matches = matches.filter((m) => m.tour === "WTA");
  else if (_tnSub === "itf") matches = matches.filter((m) => (m.tour || "").startsWith("ITF"));
  else if (_tnSub === "edges") matches = matches.filter((m) =>
    [m.a.edge, m.b.edge].some((e) => e != null && e >= 4));
  else if (_tnSub === "live") {
    matches = matches.filter((m) => m.live);
    matches.sort((x, y) => (y.upset_score || 0) - (x.upset_score || 0));
  } else if (_tnSub === "upsets") {
    matches = matches.filter((m) => m.upset);
    matches.sort((x, y) => (y.upset_score || 0) - (x.upset_score || 0));
  }
  const q = ($("tnSearch")?.value || "").trim().toLowerCase();
  if (q) matches = matches.filter((m) =>
    (m.a.name + " " + m.b.name + " " + (m.tour || "") + " " + (m.tournament || "")
      + " " + (m.kalshi_series || "") + " " + ((m.live || {}).tournament || ""))
      .toLowerCase().includes(q));
  const liveBit = d.n_live ? ` · <b style="color:#e5484d">🔴 ${d.n_live} live</b>` : "";
  const upBit = d.n_upsets ? ` · <b style="color:#e5484d">🚨 ${d.n_upsets} favorite${d.n_upsets > 1 ? "s" : ""} trailing</b>` : "";
  const playBit = d.n_play != null && d.n_play < d.n_matches ? ` <span class="small" style="color:var(--muted)">(${d.n_play} with a model read, rest are markets not open yet)</span>` : "";
  $("tnSummary").innerHTML = `<b>${d.n_matches} matches</b>${liveBit}${upBit}${playBit}. Model = serve/return rates from charted matches → point-by-point sim (with <b>recent-match fatigue</b>), <b>ensembled with our own Elo</b>. Each card shows <b>where to find it on Kalshi</b> (series + tournament). A heavy favorite still loses sometimes — the <b>1-in-N</b> tag is the real single-match upset rate. The green <b>✅ Lean</b> is the side to look at. Edge = fair win% − Kalshi ask.`;
  if (!matches.length) {
    const msg = _tnSub === "live" ? "No tracked matches on court right now."
      : _tnSub === "upsets" ? "No big favorites trailing right now — check back during play."
      : q ? `No matches for “${q}”.` : "No matches in this view.";
    $("tnResults").innerHTML = `<div class="empty">${msg}</div>`; return;
  }
  const tierTag = { high: ['🟢', 'High confidence'], medium: ['🟡', 'Medium confidence'], thin: ['🔴', 'Thin data'], elo: ['📊', 'Elo (recent results)'], market: ['⚪', 'Market only'] };
  const kalshiWhere = (m) => {
    const parts = [m.kalshi_series, m.tournament].filter(Boolean);
    return parts.length
      ? `<div class="tn-where" title="find this match on Kalshi under this series → tournament">📍 Kalshi: <b>${parts.join(" · ")}</b></div>` : "";
  };
  $("tnResults").innerHTML = matches.map((m) => {
    const a = m.a, b = m.b;
    const ts = m.total_sets || {};
    const setsLine = Object.keys(ts).length
      ? Object.entries(ts).map(([k, v]) => `${k} sets ${v}%`).join(" · ") : "";
    const games = m.mean_games != null
      ? `<span class="tn-chip" title="model expected total games">~${m.mean_games} games</span>` : "";
    const aces = m.aces_total ? `<span class="tn-chip" title="model expected total aces">~${m.aces_total} aces</span>` : "";
    const distance = ts["3"] != null && m.best_of === 3
      ? `<span class="tn-chip" title="probability the match goes 3 sets">3-set ${ts["3"]}%</span>` : "";
    const insights = (m.insights || []).map((i) => `<div class="tn-insight">${i}</div>`).join("");
    const [tEmoji, tText] = tierTag[m.conf_tier] || tierTag.thin;
    const L = m.lean;
    const strong = L && L.strength >= 6;
    const lean = L
      ? `<div class="tn-lean${strong ? " strong" : ""}" title="best edge, discounted by how much charting backs it">
           ${strong ? "⭐ " : ""}✅ Lean: <b>${L.pick}</b> to win — ${L.fair_win}% vs ${L.cents}¢ <span class="ev pos">+${L.edge}</span></div>`
      : (m.modeled === false
        ? `<div class="tn-lean none">Market-priced — no charting data on these players, so we defer to Kalshi's odds</div>`
        : `<div class="tn-lean none">No edge — model agrees with the market</div>`);
    const lv = m.live;
    const liveChip = lv
      ? `<span class="tn-livechip">🔴 LIVE ${lv.sets_a}–${lv.sets_b}${lv.cur ? ` (${lv.cur[0]}-${lv.cur[1]})` : ""} · ${lv.detail || lv.score}</span>` : "";
    // Live in-match win probability from the current score (the point-by-point
    // sim re-run from here) + the live edge vs Kalshi's current ask.
    const liveWin = (lv && lv.p_a != null)
      ? `<div class="tn-liveprob" title="win probability from the CURRENT score — the sim re-run live">⏱️ Live: <b>${a.name.split(" ").pop()} ${lv.p_a}%</b> · <b>${b.name.split(" ").pop()} ${lv.p_b}%</b>${
          (lv.edge_a != null && Math.abs(lv.edge_a) >= 5) ? ` <span class="ev ${lv.edge_a >= 0 ? "pos" : "neg"}">${a.name.split(" ").pop()} ${lv.edge_a >= 0 ? "+" : ""}${lv.edge_a}</span>` : ""}${
          (lv.edge_b != null && Math.abs(lv.edge_b) >= 5) ? ` <span class="ev ${lv.edge_b >= 0 ? "pos" : "neg"}">${b.name.split(" ").pop()} ${lv.edge_b >= 0 ? "+" : ""}${lv.edge_b}</span>` : ""}</div>` : "";
    const up = m.upset;
    const liveNote = up && up.fav_live_pct != null
      ? `still <b>${up.fav_live_pct}% live</b> vs ${up.fav_cents}¢ — market overshot`
      : `the market overshoots on a big name dropping a set`;
    const upBanner = up
      ? `<div class="tn-upset">🚨 <b>${up.fav}</b> is ${up.note} (sets ${up.sets}${up.fav_cents != null ? ` · ${up.fav_cents}¢` : ""}) but ${liveNote}.</div>` : "";
    const unopened = m.tier === "unopened";
    const leanBlock = unopened
      ? `<div class="tn-lean none">⚪ Market not open on Kalshi yet — both sides quoted high (no two-sided price). Shown so you can find it; check back closer to match time.</div>`
      : lean;
    const startTag = (!m.live && m.start && fmtStartTime(m.start))
      ? `<span class="starttime" title="scheduled start (Mountain Time)">🕒 ${fmtStartTime(m.start)}</span> ` : "";
    return `<div class="tn-match${up ? " upsetcard" : ""}${unopened ? " tn-unopened" : ""}">
        <div class="tn-mhead">${m.tour} · ${m.surface} · Bo${m.best_of} ${startTag}${liveChip}
          <span class="tn-tier" title="${tText} — how much charted history backs this read">${tEmoji} ${tText}</span></div>
        ${kalshiWhere(m)}
        ${liveWin}
        ${upBanner}
        ${_tnPlayer(a)}${_tnPlayer(b)}
        ${leanBlock}
        <div class="tn-derived">${games}${distance}${aces}${setsLine ? `<span class="tn-chip">${setsLine}</span>` : ""}</div>
        ${insights ? `<div class="tn-insights">${insights}</div>` : ""}
      </div>`;
  }).join("");
}

// ---- World Cup ----
let _wcData = null, _wcSub = "futures";
// ---- League of Legends (esports) ------------------------------------------
let _lolData = null;
let _lolp6 = { picks: [], payouts: {}, sel: new Set() };

function initLoL() {
  if (!$("lolResults").dataset.loaded) { $("lolResults").dataset.loaded = "1"; loadLoL(); }
  document.querySelectorAll("#lolSubtabs .subtab").forEach((b) => {
    if (b.dataset.wired) return;
    b.dataset.wired = "1";
    b.addEventListener("click", () => {
      document.querySelectorAll("#lolSubtabs .subtab").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      const s = b.dataset.lolsub;
      $("lolResults").classList.toggle("hidden", s !== "matches");
      $("lolPick6").classList.toggle("hidden", s !== "pick6");
      $("lolFutures").classList.toggle("hidden", s !== "futures");
      if (s === "futures") initLoLFutures();
    });
  });
  $("lolFutBtn")?.addEventListener("click", loadLoLFutures);
}

async function loadLoL(attempt) {
  attempt = attempt || 0;
  const box = $("lolResults");
  if (!attempt) box.innerHTML = `<div class="empty">Building the pro slate + per-player history in the background — the Leaguepedia API is rate-limited, so the first load takes ~30–60s. This refreshes automatically.</div>`;
  try {
    const d = await (await fetch("/api/lol")).json();
    if (d.error || !(d.matches && d.matches.length)) {
      if (attempt < 9) {
        if (attempt >= 1) box.innerHTML = `<div class="empty">Still assembling the slate… (the wiki API pages slowly) — auto-retrying.</div>`;
        setTimeout(() => loadLoL(attempt + 1), 9000);
        return;
      }
      box.innerHTML = `<div class="empty">${d.error || "No LoL data available."}</div>`;
      return;
    }
    _lolData = d;
    _lolp6 = { picks: d.picks || [], payouts: d.payouts || {}, sel: new Set() };
    renderLoL();
    renderLoLPick6();
  } catch (e) {
    if (attempt < 9) { setTimeout(() => loadLoL(attempt + 1), 9000); return; }
    box.innerHTML = `<div class="empty">LoL unavailable.</div>`;
  }
}

function renderLoL() {
  const box = $("lolResults");
  const ms = (_lolData && _lolData.matches) || [];
  $("lolSummary").innerHTML = ms.length
    ? `<span class="leanchip">${ms.length} matches</span> <span class="leanchip">${(_lolp6.picks || []).length} Pick 6 leans</span>` : "";
  if (!ms.length) { box.innerHTML = `<div class="empty">No matches loaded yet — retry in a moment (the wiki API rate-limits cold loads).</div>`; return; }
  const side = (r) => (r || []).map((p) => `<div class="lol-prow">
      <span class="lol-role">${p.role || "—"}</span>
      <span class="lol-pname">${p.player}${p.champs && p.champs.length ? `<span class="lol-champs">${p.champs.slice(0, 3).join(" · ")}</span>` : ""}</span>
      <span class="lol-stat">K <b>${p.kills}</b></span>
      <span class="lol-stat">A <b>${p.assists}</b></span>
      <span class="lol-stat">CS <b>${p.cs}</b></span>
    </div>`).join("");
  box.innerHTML = ms.map((m) => {
    const wp = (m.win1 != null)
      ? `<div class="lol-wp"><span class="lol-wpbar"><span style="width:${m.win1}%"></span></span>
         <span class="small"><b>${m.team1}</b> ${m.win1}% — ${m.win2}% <b>${m.team2}</b> · win the bo${m.bo}</span></div>` : "";
    return `<div class="lol-match">
      <div class="lol-mhead"><span class="legtag">${m.league}</span> <b>${m.team1}</b> <span class="small">vs</span> <b>${m.team2}</b> <span class="small">· bo${m.bo} · ${m.date}</span></div>
      ${wp}
      <div class="lol-teams">
        <div class="lol-team"><div class="lol-thdr">${m.team1}</div>${side(m.roster1)}</div>
        <div class="lol-team"><div class="lol-thdr">${m.team2}</div>${side(m.roster2)}</div>
      </div></div>`;
  }).join("");
}

let _lolFutWired = false;
function initLoLFutures() {
  const sel = $("lolFutSel");
  const ts = (_lolData && _lolData.tournaments) || [];
  if (!ts.length) { sel.innerHTML = `<option value="">— load the slate first —</option>`; return; }
  if (sel.dataset.filled === "1") return;
  sel.dataset.filled = "1";
  sel.innerHTML = ts.map((t) => `<option value="${encodeURIComponent(t.page)}">${t.league} — ${t.label}</option>`).join("");
}
async function loadLoLFutures() {
  const box = $("lolFutResults"), page = $("lolFutSel").value;
  if (!page) { box.innerHTML = `<div class="empty">Load the slate first.</div>`; return; }
  box.innerHTML = `<div class="empty">Simulating the tournament (Elo + bracket, thousands of runs)…</div>`;
  try {
    const d = await (await fetch(`/api/lol/futures?page=${page}`)).json();
    if (d.error) { box.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    const rows = (d.teams || []).filter((t) => t.champion > 0 || t.playoffs > 1).map((t, i) => `
      <div class="lol-frow">
        <span class="lol-frank">${i + 1}</span>
        <span class="lol-fteam">${t.team} <span class="small" style="color:var(--faint)">Elo ${t.elo}</span></span>
        <span class="lol-fstat"><span class="small">playoffs</span> <b>${t.playoffs}%</b></span>
        <span class="lol-fstat"><span class="small">champion</span> <b class="ev pos">${t.champion}%</b></span>
      </div>`).join("");
    box.innerHTML = `<div class="small" style="margin-bottom:6px">${d.n_teams} teams · ${d.games_left} games left · ${nf(d.n_sims)} sims. <i>${d.note}</i></div>${rows}`;
  } catch (e) { box.innerHTML = `<div class="empty">Futures unavailable.</div>`; }
}

function toggleLoLPick6(i) {
  const inp = document.querySelectorAll("#lolPick6 .p6row input")[i];
  if (_lolp6.sel.has(i)) _lolp6.sel.delete(i);
  else if (_lolp6.sel.size < 6) _lolp6.sel.add(i);
  else { if (inp) inp.checked = false; return; }
  const row = document.querySelectorAll("#lolPick6 .p6row")[i];
  if (row) row.classList.toggle("on", _lolp6.sel.has(i));
  renderLoLP6Tally();
}
function renderLoLPick6() {
  const box = $("lolPick6");
  if (!_lolp6.picks.length) { box.innerHTML = `<div class="empty">No Pick 6 leans yet — load the slate first.</div>`; return; }
  const rows = _lolp6.picks.map((p, i) => {
    const on = _lolp6.sel.has(i);
    return `<label class="p6row${on ? " on" : ""}">
      <input type="checkbox" ${on ? "checked" : ""} onchange="toggleLoLPick6(${i})">
      <span class="p6side ${p.side === "More" ? "more" : "less"}">${p.side} ${p.line}</span>
      <span class="p6name">${p.player} <span class="p6stat">${p.stat} · ${p.role}</span></span>
      <span class="p6game">${p.league}</span>
      <span class="p6prob"><b>${p.prob}%</b><span class="p6proj">proj ${p.proj}</span></span>
    </label>`;
  }).join("");
  box.innerHTML = `<div id="lolP6tally" class="p6tally"></div><div class="small" style="margin:2px 0 8px">Kills / assists / CS from a correlated per-map sim (kills zero-sum across the two teams). DK &amp; PrizePicks set the line — take our More/Less where it clears theirs.</div><div class="p6list">${rows}</div>`;
  renderLoLP6Tally();
}
function renderLoLP6Tally() {
  const t = $("lolP6tally");
  if (!t) return;
  const n = _lolp6.sel.size;
  if (n < 2) { t.innerHTML = `<span class="small">Pick <b>2–6</b> props (all must hit). ${n} selected.</span>`; return; }
  let prob = 1;
  const sameM = {};
  _lolp6.sel.forEach((i) => { prob *= _lolp6.picks[i].prob / 100; sameM[_lolp6.picks[i].matchup] = (sameM[_lolp6.picks[i].matchup] || 0) + 1; });
  const pay = _lolp6.payouts[String(n)] || null;
  const ev = pay ? Math.round((prob * pay - 1) * 100) : null;
  const corr = Object.values(sameM).some((c) => c > 1)
    ? ` <span class="small" style="color:var(--muted)">⚠ picks share a match — correlation shifts the true chance</span>` : "";
  t.innerHTML = `<b>${n}-pick</b> · all-hit chance <b>${(prob * 100).toFixed(1)}%</b>${pay ? ` · pays <b>${pay}×</b> · EV <b class="${ev >= 0 ? "ev pos" : "ev neg"}">${ev >= 0 ? "+" : ""}${ev}%</b>` : ""}${corr}`;
}

function initWorldCup() {
  if (!$("wcResults").dataset.loaded) { $("wcResults").dataset.loaded = "1"; loadWorldCup(); }
  document.querySelectorAll("#wcSubtabs .subtab").forEach((b) => {
    if (b.dataset.wired) return;
    b.dataset.wired = "1";
    b.addEventListener("click", () => {
      document.querySelectorAll("#wcSubtabs .subtab").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      _wcSub = b.dataset.wcsub;
      const p6 = _wcSub === "pick6";
      $("wcResults").classList.toggle("hidden", p6);
      $("wcPick6").classList.toggle("hidden", !p6);
      if (p6) { initWCPick6(); return; }
      renderWorldCup();
    });
  });
}
let _wcP6 = null;
function initWCPick6() {
  if ($("wcPick6").dataset.loaded) return;
  $("wcPick6").dataset.loaded = "1";
  loadWCPick6();
}
async function loadWCPick6() {
  const box = $("wcPick6");
  box.innerHTML = `<div class="empty">Building the World Cup player prop board…</div>`;
  try {
    const d = await (await fetch("/api/worldcup/pick6")).json();
    if (d.error || !(d.picks && d.picks.length)) { box.innerHTML = `<div class="empty">${d.error || "No player props yet."}</div>`; return; }
    _wcP6 = d;
    renderWCPick6();
  } catch (e) { box.innerHTML = `<div class="empty">World Cup props unavailable.</div>`; }
}
function renderWCPick6() {
  const d = _wcP6; if (!d) return;
  const rows = d.picks.map((p) => `<div class="p6row p6row-nfl" style="cursor:default">
      <span class="p6side ${p.side === "More" ? "more" : "less"}">${p.side} ${p.line}</span>
      <span class="p6name">${p.player} <span class="p6stat">${p.stat} · ${p.team}</span></span>
      <span class="p6game">${p.matchup}</span>
      <span class="p6prob"><b>${p.prob}%</b><span class="p6proj">proj ${p.proj}</span></span>
    </div>`).join("");
  const mchips = (d.matches || []).map((m) => `<span class="leanchip">${m.matchup}</span>`).join(" ");
  $("wcPick6").innerHTML =
    `<div class="small" style="margin:2px 0 6px">${mchips}</div>
     <div class="small" style="margin:0 0 8px;color:var(--muted)">${d.note}</div>
     <div class="p6list">${rows}</div>`;
}
async function loadWorldCup() {
  $("wcResults").innerHTML = `<div class="empty">Simulating the rest of the tournament…</div>`;
  try {
    const d = await (await fetch("/api/worldcup")).json();
    if (d.error) { $("wcResults").innerHTML = `<div class="empty">${d.error}</div>`; return; }
    _wcData = d;
    renderWorldCup();
  } catch (e) { $("wcResults").innerHTML = `<div class="empty">Failed to load.</div>`; }
}
function _wcEdge(e) {
  if (e == null) return "—";
  return `<span class="ev ${e >= 0 ? "pos" : "neg"}">${e >= 0 ? "+" : ""}${e}</span>`;
}
function _wcPx(cents) { return cents != null ? `${cents}¢` : "—"; }
function renderWorldCup() {
  const d = _wcData; if (!d) return;
  $("wcSummary").innerHTML = `Simulated <b>${d.n_sims.toLocaleString()}</b> tournaments · model = national-team Elo updated through results played, Poisson match engine. Edges are model − market.`;
  if (_wcSub === "matches") return renderWCMatches(d);
  // Futures: champion board with Kalshi + Poly edges.
  const rows = d.teams.filter((t) => t.advance_pct > 0).map((t, i) => {
    const k = (t.champion_market || {}).kalshi || {};
    const p = (t.champion_market || {}).poly || {};
    return `<div class="futrow wcrow">
      <span class="fr-rank">${i + 1}</span>
      <span class="fr-team">${t.name} <span class="small" style="color:var(--muted)">${t.group}</span></span>
      <span class="fr-num">${t.champion_pct}%</span>
      <span class="fr-num">${_wcPx(k.cents)}</span><span class="fr-num">${_wcEdge(k.edge)}</span>
      <span class="fr-num">${_wcPx(p.cents)}</span>
      <span class="fr-num">${t.finalist_pct}%</span>
      <span class="fr-num">${t.advance_pct}%</span></div>`;
  }).join("");
  $("wcResults").innerHTML = `
    <div class="futrow wcrow rchead">
      <span class="fr-rank">#</span><span class="fr-team">Team</span>
      <span class="fr-num">Champ</span><span class="fr-num">Kalshi</span><span class="fr-num">Edge</span>
      <span class="fr-num">Poly</span><span class="fr-num">Final</span><span class="fr-num">Advance</span></div>
    ${rows}`;
}
function renderWCMatches(d) {
  if (!d.matches.length) { $("wcResults").innerHTML = `<div class="empty">No upcoming matches with set teams.</div>`; return; }
  const rows = d.matches.map((m) => {
    const w = (m.markets || {}).winner || {};
    const o = (m.markets || {}).over25, bt = (m.markets || {}).btts;
    const cell = (pct, mk) => `<span class="fr-num">${pct}%</span><span class="fr-num">${mk ? _wcEdge(mk.edge) : "—"}</span>`;
    return `<div class="wcmatch">
      <div class="wcmatch-h"><b>${m.home}</b> v <b>${m.away}</b>
        <span class="small" style="color:var(--muted)">${m.date} · ${m.stage === "group" ? "group" : "knockout"} · xG ${m.mean_goals}</span></div>
      <div class="futrow wcmrow">
        <span class="fr-team">${m.home} win</span>${cell(m.p_home, (w.home))}
        <span class="fr-team">Draw</span>${cell(m.p_draw, (w.draw))}
        <span class="fr-team">${m.away} win</span>${cell(m.p_away, (w.away))}</div>
      <div class="futrow wcmrow">
        <span class="fr-team">Over 2.5</span>${cell(m.over25, o)}
        <span class="fr-team">BTTS</span>${cell(m.btts_pct, bt)}
        <span class="fr-team"></span><span class="fr-num"></span><span class="fr-num"></span></div>
    </div>`;
  }).join("");
  $("wcResults").innerHTML = rows;
}

function _pxCells(x) {  // Kalshi ¢ + edge cells (— when unmatched)
  const k = x.kalshi_cents != null ? `${x.kalshi_cents}¢` : "—";
  const e = x.edge != null ? `<span class="${x.edge >= 0 ? "ev pos" : "ev neg"}">${x.edge >= 0 ? "+" : ""}${x.edge}</span>` : "—";
  return `<span class="fr-num">${k}</span><span class="fr-num">${e}</span>`;
}

function renderRaceMarket() {
  const d = _raceData; if (!d) return;
  const isF1 = d.sport === "f1", m = _raceMarket;
  let html;
  if (m === "drivers") {
    html = `<div class="futrow rcrowD rchead"><span class="fr-rank">#</span><span class="fr-team">Driver</span>
      <span class="fr-num">Title</span><span class="fr-num">Kalshi</span><span class="fr-num">Edge</span>
      <span class="fr-num">Proj pts</span><span class="fr-num">Wins</span></div>`
      + d.drivers.filter((x) => x.title_pct > 0 || x.exp_wins >= 0.3).slice(0, 24).map((x, i) => `
        <div class="futrow rcrowD"><span class="fr-rank">${i + 1}</span>
          <span class="fr-team"><b>${x.name}</b>${isF1 ? `<span class="small"> ${x.constructor}</span>` : ""}</span>
          <span class="fr-num"><b>${x.title_pct}%</b></span>${_pxCells(x)}
          <span class="fr-num">${x.proj_points}</span><span class="fr-num">${x.exp_wins}</span></div>`).join("");
  } else if (m === "constructors") {
    html = `<div class="futrow rcrowR rchead"><span class="fr-rank">#</span><span class="fr-team">Constructor</span><span class="fr-num">Title</span><span class="fr-num"></span><span class="fr-num"></span></div>`
      + (d.constructors || []).filter((c) => c.title_pct >= 0.1).map((c, i) =>
        `<div class="futrow rcrowR"><span class="fr-rank">${i + 1}</span><span class="fr-team"><b>${c.name}</b></span>
         <span class="fr-num"><b>${c.title_pct}%</b></span><span class="fr-num"></span><span class="fr-num"></span></div>`).join("");
  } else {
    const [kind, idx] = m.split(":");
    const race = d.races[+idx];
    const list = kind === "pole" ? race.pole : race.winner;
    const note = race.priced ? "" : ` <span class="small" style="color:var(--muted)">— model only; Kalshi prices appear once it's the next race</span>`;
    const wx = race.wet_prob != null
      ? `<div class="small" style="color:var(--muted);margin:-2px 0 6px">🌧️ ${Math.round(race.wet_prob * 100)}% wet-race risk${race.avg_wind ? ` · 💨 ${race.avg_wind} km/h avg` : ""}${race.circuit ? ` · ${race.circuit}` : ""} <span style="color:var(--faint)">(historical race-day climate; wet races scramble the order)</span></div>` : "";
    const rt = fmtStartTime(race.start);
    const startLine = rt ? `<div class="starttime" style="margin:0 0 5px">🕒 Green flag ${rt}</div>` : "";
    html = `<div class="teamhdr" style="margin:0 0 2px">${race.name} — ${kind === "pole" ? "🏁 Pole" : "🏆 Race winner"} odds${note}</div>${startLine}${wx}`
      + `<div class="futrow rcrowR rchead"><span class="fr-rank">#</span><span class="fr-team">Driver</span><span class="fr-num">Model</span><span class="fr-num">Kalshi</span><span class="fr-num">Edge</span></div>`
      + list.map((x, i) => `<div class="futrow rcrowR"><span class="fr-rank">${i + 1}</span>
          <span class="fr-team"><b>${x.name}</b>${x.team ? `<span class="small"> ${x.team}</span>` : ""}</span>
          <span class="fr-num"><b>${x.pct}%</b></span>${_pxCells(x)}</div>`).join("");
  }
  $("raceTable").innerHTML = html;
}

async function startDeepRun() {
  const btn = $("deepBtn");
  if (btn) { btn.disabled = true; btn.textContent = "starting…"; }
  try { await fetch("/api/baseball/futures/deep", { method: "POST" }); } catch (e) {}
  watchFeatured();
}

function deepBar(s) {
  const pct = Math.max(0, Math.min(100, s.pct || 0));
  const eta = s.eta_sec != null ? `~${Math.max(1, Math.ceil(s.eta_sec / 60))} min left` : "estimating…";
  return `<div class="deepbar-wrap">
    <div class="small">⚡ <b>Deep pitch-by-pitch sim running</b> — every pitch, hit &amp; walk across <b>${(s.total || 0).toLocaleString()}</b> seasons.
      <span style="color:var(--muted)">Keep using the app; it runs in the background and caches when done.</span></div>
    <div class="deepbar"><div class="deepbar-fill" style="width:${pct}%"></div></div>
    <div class="small" style="display:flex;justify-content:space-between">
      <span>${(s.done || 0).toLocaleString()} / ${(s.total || 0).toLocaleString()} seasons · ${eta}</span><span><b>${pct}%</b></span></div>
  </div>`;
}

// Wall-clock time a cached sim was generated (seconds since epoch) from its age.
const _genTime = (age) => (Date.now() / 1000) - (age || 0);

// True while the Featured sub-view is the one on screen (so we don't reload a
// board the user isn't looking at).
function _featuredVisible() {
  const tab = $("tab-sports");
  if (!tab || tab.classList.contains("hidden")) return false;
  const sub = document.querySelector("#tab-sports .sub-featured");
  return !!sub && !sub.classList.contains("hidden");
}

// Watch the current sport's sim: drive the MLB deep progress bar while a run is in
// flight, and — crucially — reload the board whenever a NEWER run lands (kicked by
// the button or the nightly scheduler), so the page updates itself without a manual
// refresh. Keeps a slow heartbeat when idle so a midnight rerun is picked up.
async function watchFeatured() {
  clearTimeout(_deepTimer);
  const prog = $("deepProg");
  let delay = 60000;
  try {
    if (_featSport === "mlb") {
      const s = await (await fetch("/api/baseball/futures/deep/status")).json();
      if (s.running) {
        if (prog) prog.innerHTML = deepBar(s);
        delay = 3000;
      } else {
        if (prog) prog.innerHTML = "";
        const newGen = s.age_sec != null ? _genTime(s.age_sec) : 0;
        const firstDeep = !_boardData || _boardData.engine !== "deep";
        const newer = newGen > _featGenTime + 20;      // a fresh run finished
        if (s.ready && (firstDeep || newer) && _featuredVisible()) {
          loadFeatured(); return;                      // loadFeatured re-arms the watcher
        }
      }
    } else {
      const key = _featSport;                          // "f1" | "nascar"
      const st = (await (await fetch("/api/sim/status")).json())[key] || {};
      if (st.running) {
        delay = 5000;
      } else if (st.age_sec != null && _genTime(st.age_sec) > _featGenTime + 20 && _featuredVisible()) {
        loadFeatured(); return;
      }
    }
  } catch (e) {}
  _deepTimer = setTimeout(watchFeatured, delay);
}

function renderFeatured(d) {
  if (d.sport === "nfl") {
    $("featuredSummary").innerHTML =
      `<div class="small" style="margin-bottom:6px">Simulated <b>${d.n_sims.toLocaleString()}</b> seasons · ${d.n_games_left} games left · <b>roster-aware model</b> (projected wins → game-by-game season + full playoff bracket). Pick a market and search any team — the count is how many of the ${d.n_sims.toLocaleString()} simulated seasons that team won it, next to our model %, Kalshi and Polymarket.</div>`;
    const groupsN = {};
    d.order.forEach((k) => { const m = d.markets[k]; (groupsN[m.group] = groupsN[m.group] || []).push([k, m.label]); });
    const ogN = Object.entries(groupsN).map(([g, items]) =>
      `<optgroup label="${g}">${items.map(([k, lbl]) => `<option value="${k}">${lbl}</option>`).join("")}</optgroup>`).join("");
    const curN = (_featMarket && d.markets[_featMarket]) ? _featMarket : d.order[0];
    _featMarket = curN;
    $("featuredResults").innerHTML = `
      <div class="futctl">
        <label class="small">Market</label>
        <select id="futMarket" onchange="_featMarket=this.value;renderFeaturedTable()">${ogN}</select>
        <input id="futSearch" placeholder="🔍 search team…" oninput="renderFeaturedTable()" autocomplete="off"/>
      </div>
      <div class="small" style="margin:2px 0 6px;color:var(--muted)">Click a team for its projected player stat lines (Sleeper projections; real stats blend in during the season).</div>
      <div id="futTeamDetail"></div>
      <div id="futTable"></div>`;
    $("futMarket").value = curN;
    renderFeaturedTable();
    return;
  }
  const engineNote = d.engine === "deep"
    ? `<b>deep pitch-by-pitch engine</b> — game-by-game, run-by-run`
    : `fast model (expected runs → Pythagorean)`;
  const deepCtl = d.engine === "deep"
    ? `<span class="deepbadge">⚡ deep engine · ${d.n_sims.toLocaleString()} seasons</span>
       <span class="small" style="color:var(--muted);margin-left:6px">updated <b>${agoStr(d.age_sec)}</b> · reruns nightly (auto-updates here)</span>
       <button class="track-mini" style="margin-left:6px" onclick="startDeepRun()">↻ rerun now</button>`
    : `<button class="track-mini" id="deepBtn" onclick="startDeepRun()">⚡ Run deep pitch-by-pitch sim (~4,000 seasons)</button>`;
  $("featuredSummary").innerHTML =
    `<div class="small" style="margin-bottom:6px">Simulated <b>${d.n_sims.toLocaleString()}</b> seasons · ${d.n_games_left} games left · ${engineNote}. Pick a market and search any team — the count is how many of the ${d.n_sims.toLocaleString()} simulated seasons that team won it, next to our model %, Kalshi and Polymarket.</div>
     <div style="margin-bottom:8px">${deepCtl}</div>
     <div id="deepProg" style="margin-bottom:8px"></div>`;
  // Grouped market dropdown (Titles / Season win totals).
  const groups = {};
  d.order.forEach((k) => { const m = d.markets[k]; (groups[m.group] = groups[m.group] || []).push([k, m.label]); });
  const optgroups = Object.entries(groups).map(([g, items]) =>
    `<optgroup label="${g}">${items.map(([k, lbl]) => `<option value="${k}">${lbl}</option>`).join("")}</optgroup>`).join("");
  const cur = (_featMarket && d.markets[_featMarket]) ? _featMarket : d.order[0];
  _featMarket = cur;
  $("featuredResults").innerHTML = `
    <div class="futctl">
      <label class="small">Market</label>
      <select id="futMarket" onchange="_featMarket=this.value;renderFeaturedTable()">${optgroups}</select>
      <input id="futSearch" placeholder="🔍 search team…" oninput="renderFeaturedTable()" autocomplete="off"/>
    </div>
    ${d.engine === "deep" ? `<div class="small" style="margin:2px 0 6px;color:var(--muted)">Click a team for its simulated season stat lines.</div>` : ""}
    <div id="futTeamDetail"></div>
    <div id="futTable"></div>`;
  $("futMarket").value = cur;
  renderFeaturedTable();
}

// MLB statsapi ids -> labels (stable ids; lets the cached board show them
// without waiting for a season-sim rerun).
const _MLB_DIV = { 200: "AL West", 201: "AL East", 202: "AL Central",
                   203: "NL West", 204: "NL East", 205: "NL Central" };
const _MLB_LG = { 103: "AL", 104: "NL" };
function _divChip(r) {
  const dv = _MLB_DIV[r.division] || (typeof r.division === "string" ? r.division : null);
  if (!dv) return "";
  const al = r.league === 103 || dv.startsWith("AL") || dv.startsWith("AFC");
  return ` <span class="divchip ${al ? "al" : "nl"}" title="division — only ONE team per division can win it, so never put two same-division teams in the same division-winner slip">${dv}</span>`;
}
function renderFeaturedTable() {
  const d = _boardData; if (!d) return;
  const m = d.markets[_featMarket]; if (!m) return;
  const q = (($("futSearch") || {}).value || "").toLowerCase().trim();
  let rows = m.teams;
  if (q) rows = rows.filter((r) => r.team.toLowerCase().includes(q) || (r.abbr || "").toLowerCase().includes(q));
  // Bold the cheaper book (the one you'd buy our side on).
  const bk = (r, book) => {
    const c = book === "Kalshi" ? r.kalshi_cents : r.poly_cents;
    if (c == null) return "—";
    return r.best_book === book ? `<b>${c}¢</b>` : `${c}¢`;
  };
  const maxc = Math.max(1, ...m.teams.map((r) => r.count));
  const head = `<div class="futrow futhead">
    <span class="fr-rank">#</span><span class="fr-team">Team</span>
    <span class="fr-count">Won (of ${d.n_sims.toLocaleString()} sims)</span>
    <span class="fr-num">Our</span><span class="fr-num">Kalshi</span><span class="fr-num">Poly</span><span class="fr-num">Edge</span></div>`;
  const deep = d.engine === "deep";
  const body = rows.map((r, i) => {
    const w = Math.round(100 * r.count / maxc);
    const ecls = r.edge == null ? "" : r.edge >= 0 ? "ev pos" : "ev neg";
    const click = (deep || d.sport === "nfl") && r.abbr ? ` futrow-click" onclick="openTeamDetail('${r.abbr}')` : "";
    return `<div class="futrow${click}">
      <span class="fr-rank">${i + 1}</span>
      <span class="fr-team"><b>${r.team}</b>${_divChip(r)}<span class="small"> ${r.wins}-${r.losses} · proj ${r.proj_wins}</span></span>
      <span class="fr-count"><span class="fr-bar" style="width:${w}%"></span><span class="fr-ct">${r.count.toLocaleString()} <span class="small">(${r.model_pct}%)</span></span></span>
      <span class="fr-num"><b>${r.model_pct}%</b></span>
      <span class="fr-num">${bk(r, "Kalshi")}</span>
      <span class="fr-num">${bk(r, "Polymarket")}</span>
      <span class="fr-num ${ecls}">${r.edge == null ? "—" : (r.edge >= 0 ? "+" : "") + r.edge}</span>
    </div>`;
  }).join("");
  const nflB = d.sport === "nfl";
  const mx = _featMarket === "division"
    ? `<div class="small" style="color:var(--muted);margin:2px 0 6px">⚠️ One winner per division — two teams with the <b>same division chip</b> can never both hit. Don't pair them in a slip.</div>`
    : _featMarket === "pennant"
      ? `<div class="small" style="color:var(--muted);margin:2px 0 6px">⚠️ ${nflB ? "One champion per conference — two <b>AFC</b> (or two <b>NFC</b>) teams can never both hit." : "One pennant per league — two <b>AL</b> (or two <b>NL</b>) teams can never both hit."} Don't pair them in a slip.</div>`
      : "";
  $("futTable").innerHTML = mx + head + (body || `<div class="empty">No team matches “${q}”.</div>`);
}

async function openTeamDetail(abbr) {
  const box = $("futTeamDetail");
  if (!box) return;
  const nfl = _boardData && _boardData.sport === "nfl";
  box.innerHTML = `<div class="small" style="padding:8px">Loading ${abbr} ${nfl ? "player projections" : "simulated season"}…</div>`;
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  try {
    const d = await (await fetch(nfl ? `/api/nfl/team?abbr=${abbr}` : `/api/baseball/team?abbr=${abbr}`)).json();
    box.innerHTML = d.error ? `<div class="small" style="padding:8px">${d.error}</div>`
      : (nfl ? renderNflSeasonTeam(d) : renderTeamDetail(d));
  } catch (e) {
    box.innerHTML = `<div class="small" style="padding:8px">Failed to load team.</div>`;
  }
}

function renderNflSeasonTeam(d) {
  const rp = (v) => (v == null ? "" : ` <span class="rp">(${v})</span>`);
  const cell = (p, k) => `${p[k] ?? 0}${p.real ? rp(p.real[k]) : ""}`;
  const rows = (d.players || []).map((p) => `<tr>
      <td>${p.name} <span class="small">${p.pos}</span></td><td><b>${p.fpts}</b></td>
      <td>${cell(p, "pass_yd")}</td><td>${cell(p, "pass_td")}</td><td>${cell(p, "pass_int")}</td>
      <td>${cell(p, "rush_yd")}</td><td>${cell(p, "rush_td")}</td>
      <td>${cell(p, "rec")}</td><td>${cell(p, "rec_yd")}</td><td>${cell(p, "rec_td")}</td></tr>`).join("");
  return `<div class="teamdetail">
    <div class="teamdetailhead"><b>${d.team}</b> — projected <b>season-end</b> player stat lines <span class="small" style="color:var(--muted)">· ${d.source}${d.weeks_played ? ` · real stats through week ${d.weeks_played} in <span class="rp">(parentheses)</span>` : ""}</span>
      <span class="tdclose" onclick="closeTeamDetail()">✕</span></div>
    <div class="tdtbls"><div><table class="seasontbl"><thead><tr><th>Player</th><th>FPTS</th>
      <th>PaYd</th><th>PaTD</th><th>INT</th><th>RuYd</th><th>RuTD</th><th>Rec</th><th>ReYd</th><th>ReTD</th></tr></thead>
      <tbody>${rows}</tbody></table></div></div></div>`;
}
window.openTeamDetail = openTeamDetail;
window.closeTeamDetail = () => { const b = $("futTeamDetail"); if (b) b.innerHTML = ""; };

function renderTeamDetail(d) {
  const av = (x) => (x == null ? "—" : x.toFixed(3).replace(/^0/, ""));
  const rp = (v) => (v == null ? "" : ` <span class="rp">(${v})</span>`);   // real in parens
  const ilTag = (r) => r.il ? ` <span class="iltag" title="On the injured list${r.status ? " (" + r.status + ")" : ""}">🏥 IL</span>` : "";
  const bnTag = (b) => b.role === "bench"
    ? ` <span class="small" style="color:var(--faint)" title="bench — enters as a pinch hitter or fill-in starter${b.ph_g ? ` (~${b.ph_g} simulated PH appearances/season)` : ""}">BN</span>`
    : b.role === "taxi"
      ? ` <span class="small" style="color:var(--faint)" title="taxi squad (optioned to the minors) — the sim called him up when injuries drained the MLB bench">AAA</span>` : "";
  const bat = d.batting.map((b) => {
    const r = b.real || {};
    if (!b.has_sim) {   // injured, no simulated line — real stats only
      return `<tr class="ilrow"><td>${b.name}${ilTag(b)}</td><td>${av(r.avg)}</td><td>${r.ops ? av(r.ops) : "—"}</td><td>${r.h ?? "—"}</td>
        <td>${r.hr ?? "—"}</td><td>${r.r ?? "—"}</td><td>${r.rbi ?? "—"}</td><td>${r.sb ?? "—"}</td><td>${r.bb ?? "—"}</td><td>${r.k ?? "—"}</td></tr>`;
    }
    return `<tr${b.il ? ' class="ilrow"' : ""}><td>${b.name}${bnTag(b)}${ilTag(b)}</td>
      <td>${av(b.avg)}${b.real ? rp(av(r.avg)) : ""}</td><td>${b.ops != null ? av(b.ops) : "—"}${r.ops ? rp(av(r.ops)) : ""}</td><td>${b.h}${rp(r.h)}</td>
      <td>${b.hr}${rp(r.hr)}</td><td>${b.r}${rp(r.r)}</td><td>${b.rbi}${rp(r.rbi)}</td>
      <td>${b.sb ?? 0}${rp(r.sb)}</td><td>${b.bb}${rp(r.bb)}</td><td>${b.k}${rp(r.k)}</td></tr>`;
  }).join("");
  const pit = d.pitching.map((p) => {
    const r = p.real || {};
    if (!p.has_sim) {
      return `<tr class="ilrow"><td>${p.name} <span class="small">${p.role || "P"}</span>${ilTag(p)}</td>
        <td>${r.ip ?? "—"}</td><td>${r.era != null ? r.era : "—"}</td><td>${r.whip ?? "—"}</td><td>${r.fip ?? "—"}</td><td>${r.k ?? "—"}</td>
        <td>${r.bb ?? "—"}</td><td>${r.h ?? "—"}</td><td>${r.hr ?? "—"}</td></tr>`;
    }
    return `<tr${p.il ? ' class="ilrow"' : ""}><td>${p.name} <span class="small">${p.role}</span>${ilTag(p)}</td>
      <td>${p.ip}${rp(r.ip)}</td><td>${p.era != null ? p.era : "—"}${p.real && r.era != null ? rp(r.era) : ""}</td>
      <td>${p.whip != null ? p.whip : "—"}${r.whip ? rp(r.whip) : ""}</td><td>${p.fip != null ? p.fip : "—"}${r.fip ? rp(r.fip) : ""}</td>
      <td>${p.k}${rp(r.k)}</td><td>${p.bb}${rp(r.bb)}</td><td>${p.h}${rp(r.h)}</td><td>${p.hr}${rp(r.hr)}</td></tr>`;
  }).join("");
  const phNote = d.ph_primary
    ? ` · primary pinch hitter (simmed): <b>${d.ph_primary.name}</b> ~${d.ph_primary.ph_g} PH apps/season` : "";
  return `<div class="teamdetail">
    <div class="teamdetailhead"><b>${d.team}</b> — projected <b>season-end</b> totals (current + simulated remainder, averaged over ${d.n_sims.toLocaleString()} deep seasons) <span class="small" style="color:var(--muted)">· current-season stats in <span class="rp">(parentheses)</span> · 🏥 IL players at the bottom · BN = bench${phNote}</span>
      <span class="tdclose" onclick="closeTeamDetail()">✕</span></div>
    <div class="tdtbls">
      <div><div class="tdcap">⚾ Batting <span class="small" style="color:var(--muted)">season-end projection (current)</span></div><table class="seasontbl"><thead><tr><th>Hitter</th><th>AVG</th><th>OPS</th><th>H</th><th>HR</th><th>R</th><th>RBI</th><th>SB</th><th>BB</th><th>K</th></tr></thead><tbody>${bat}</tbody></table></div>
      <div><div class="tdcap">🥎 Pitching <span class="small" style="color:var(--muted)">season-end projection (current)</span></div><table class="seasontbl"><thead><tr><th>Pitcher</th><th>IP</th><th>ERA</th><th>WHIP</th><th>FIP</th><th>K</th><th>BB</th><th>H</th><th>HR</th></tr></thead><tbody>${pit}</tbody></table></div>
    </div>
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
  $("sportSummary").innerHTML = "";
  try {
    const d = await (await fetch("/api/sports/" + key)).json();
    if (d.error) { box.innerHTML = `<div class="empty">${d.error}</div>`; _sportsData = null; return; }
    if (!d.events.length) { box.innerHTML = `<div class="empty">No open ${key} markets right now.</div>`; _sportsData = null; return; }
    _sportsData = d; _sportsKey = key;
    renderSports();
  } catch (e) {
    box.innerHTML = `<div class="empty">Failed to load.</div>`;
  }
}

let _sportsData = null, _sportsKey = null;

function renderSports() {
  const d = _sportsData; if (!d) return;
  const key = _sportsKey;
  const box = $("sportResults");
  let banner = "";
  if (d.racing_locked) {
    banner = `<div class="note" style="border:1px solid var(--accent);color:var(--accent)">🔒 Grid-based win model & edge picks for racing need the ${tierLabel("pro")} tier. <button class="track-mini primary-mini" onclick="window.bumpTier('pro')">Unlock</button></div>`;
  } else if (d.grid && d.grid.available) {
    const basis = d.grid.sim_used ? `race simulator (${d.grid.sim_drivers} drivers) + grid place-differential`
      : d.grid.form_used ? "grid + recent form" : "grid";
    banner = `<div class="small" style="margin:2px 0 8px">🏁 Model using <b>${d.grid.race}</b> ${basis} (${d.grid.series}, ${d.grid.field}-car field). Edge = model win% − Kalshi price.</div>`;
  } else if (d.grid && !d.grid.available) {
    banner = `<div class="small" style="margin:2px 0 8px">🏁 ${d.grid.reason} — showing market-favorite picks until qualifying posts.</div>`;
  }
  // Summary: how many tradeable, the best value (lowest vig among liquid), arbs.
  const liquid = d.events.filter((e) => e.liquidity === "ok");
  const arbs = d.events.filter((e) => e.arbitrage_pct && e.liquidity === "ok").length;
  const vigged = liquid.filter((e) => e.overround_pct != null);
  const bestVal = vigged.length ? vigged.reduce((a, b) => b.overround_pct < a.overround_pct ? b : a) : null;
  $("sportSummary").innerHTML = `<div class="leanrow">
    <span class="leanchip">${liquid.length}/${d.events.length} liquid</span>
    ${bestVal ? `<span class="leanchip">best value: <b>${bestVal.title.slice(0, 34)}</b> (vig ${bestVal.overround_pct}%)</span>` : ""}
    ${arbs ? `<span class="leanchip warn">${arbs} possible arb${arbs === 1 ? "" : "s"}</span>` : ""}
  </div>`;
  // Liquid markets first (real prices), then chronological; optionally hide thin.
  const hideThin = $("sportHideThin").checked;
  let events = d.events.slice();
  if (hideThin) events = events.filter((e) => e.liquidity === "ok");
  const rank = { ok: 0, thin: 1, none: 2 };
  events.sort((a, b) => (rank[a.liquidity] ?? 1) - (rank[b.liquidity] ?? 1)
    || (a.close_time || 1e18) - (b.close_time || 1e18));
  if (!events.length) { box.innerHTML = banner + `<div class="empty">All ${d.events.length} markets are thin/untraded. Uncheck "hide thin" to see them.</div>`; return; }
  box.innerHTML = banner + events.map((e) => renderSportEvent(e, key)).join("");
}

// ---- Commodities scanner --------------------------------------------------
let comLoaded = false;
function renderComRow(m, basisBad) {
  const sig = m.signal;
  // In a thin book, or when our spot doesn't line up with Kalshi's ladder, the
  // modeled edge is unreliable -- show it muted as HOLD instead of a buy call.
  const trusted = !m.thin && !basisBad;
  const side = (trusted && sig.recommendation === "BUY YES") ? "YES"
    : (trusted && sig.recommendation === "BUY NO") ? "NO" : null;
  const cls = side ? "scanrow edge" : "scanrow";
  let action;
  if (side) {
    const cost = side === "YES" ? m.yes_ask : m.no_ask;
    const fair = side === "YES" ? sig.fair_yes_cents : sig.fair_no_cents;
    action = `<div class="actionline">${badge(sig.recommendation, sig.strength)}<span class="edgeval pos">+${m.best_edge}¢ edge</span></div>
      <div class="plain">✅ Buy <b>${side}</b> at <b>${cost}¢</b> → fair <b>${fair}¢</b>${sig.confidence != null ? ` · <b>${sig.confidence}%</b> confidence` : ""}</div>`;
  } else if (sig.recommendation !== "HOLD") {
    // There's a modeled edge but we don't trust it (thin / basis off).
    const why = m.thin ? "thin/untraded" : "spot vs ladder off";
    action = `<div class="actionline">${badge("HOLD", "flat")}<span class="edgeval neg">edge unreliable (${why})</span></div>
      <div class="plain">Model sees ${m.best_edge}¢ but the price isn't trustworthy here.</div>`;
  } else {
    action = `<div class="actionline">${badge("HOLD", "flat")}<span class="edgeval neg">no clear edge</span></div>
      <div class="plain">Model fair YES <b>${sig.fair_yes_cents}¢</b> vs market.</div>`;
  }
  const spStr = m.spread != null ? ` · spread ${m.spread}¢` : "";
  return `<div class="${cls}">
    <div class="scanhead">
      <div class="strike">${m.subtitle || m.ticker}${m.thin ? ` <span class="ev neg" style="font-size:.72rem">thin</span>` : ""}</div>
      <div class="small">closes in ${m.days_to_close}d · Kalshi YES ${m.yes_ask ?? "–"}¢ / NO ${m.no_ask ?? "–"}¢${spStr}</div>
    </div>
    ${action}
  </div>`;
}

function comBasisBanner(b, spot) {
  if (!b || b.center == null) {
    return `<div class="small">our spot <b>$${spot}</b> — verify it matches Kalshi's level (couldn't auto-read the ladder).</div>`;
  }
  if (b.ok === true) {
    return `<div class="small" style="color:var(--yes)">✓ spot <b>$${spot}</b> aligns with Kalshi-implied <b>$${b.center}</b> (${b.diff_pct > 0 ? "+" : ""}${b.diff_pct}%). Basis looks fine.</div>`;
  }
  if (b.ok === null) {
    return `<div class="small" style="color:var(--muted)">spot <b>$${spot}</b> vs ladder ~$${b.center} — this contract type (max/min/range) isn't directly comparable, so basis can't be auto-verified. Eyeball it.</div>`;
  }
  return `<div class="note" style="border:1px solid var(--no);color:var(--no)">⚠ Basis mismatch: our spot <b>$${spot}</b> vs Kalshi-implied <b>$${b.center}</b> (${b.diff_pct > 0 ? "+" : ""}${b.diff_pct}%). Our feed and Kalshi's settlement reference disagree, so the edges below are unreliable — don't trade off them.</div>`;
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
    // Don't trust modeled edges when the basis is off OR the contract type isn't
    // spot-comparable (max/min/range) -- the spot-GBM model doesn't fit those.
    const basisBad = d.basis && (d.basis.ok === false || d.basis.incomparable);
    const tradeable = d.markets.filter((m) => !m.thin);
    const buys = basisBad ? 0 : tradeable.filter((m) => m.signal.recommendation !== "HOLD").length;
    const head = `<div class="volbox"><div class="sellhead">
      <span class="sellaction">${d.label}</span>
      <span class="small">${buys} actionable edge${buys === 1 ? "" : "s"} · ${tradeable.length}/${d.markets.length} liquid</span>
    </div>${comBasisBanner(d.basis, d.spot)}</div>`;
    if (!d.markets.length) { box.innerHTML = head + `<div class="empty">No open ${key} contracts right now.</div>`; return; }
    box.innerHTML = head + d.markets.map((m) => renderComRow(m, basisBad)).join("");
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
    let d = await (await fetch("/api/weather/" + city)).json();
    if (d.error) { box.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    if (!d.events.length) { box.innerHTML = `<div class="empty">No open ${d.city} temperature markets right now.</div>`; return; }
    // Date dropdown: all open market days for this city (+ an "All days" option).
    const dateSel = $("wxDate");
    if (dateSel) {
      const want = dateSel.value;
      const opts = `<option value="">All days</option>` +
        d.events.map((ev) => `<option value="${ev.date}">${ev.date}</option>`).join("");
      dateSel.innerHTML = opts;
      if (d.events.some((ev) => ev.date === want) || want === "") dateSel.value = want;
    }
    const pickDate = dateSel ? dateSel.value : "";
    d = Object.assign({}, d, { events: pickDate ? d.events.filter((ev) => ev.date === pickDate) : d.events });
    const cur = d.current;
    const curBox = cur ? `<div class="volbox">
      <div class="sellhead"><span class="sellaction">🌡️ ${d.city} — live now</span>
        <span class="small">${cur.temp_f != null ? `<b>${cur.temp_f}°F</b>` : ""}${cur.high_so_far_f != null ? ` · high so far <b>${cur.high_so_far_f}°</b>` : ""}</span></div>
      <div class="small">dew point <b>${cur.dew_point_f ?? "—"}°</b> · humidity <b>${cur.humidity_pct ?? "—"}%</b> · wind <b>${cur.wind_mph ?? "—"} mph</b> · pressure <b>${cur.pressure_hpa ?? "—"} hPa</b></div>
    </div>` : "";
    box.innerHTML = curBox + d.events.map((ev) => {
      const rows = collapseRows(ev.outcomes.map((o) => {
        const ec = o.edge_cents;
        const cls = ec == null ? "" : ec >= 7 ? "ev pos" : ec <= -7 ? "ev neg" : "";
        return `<div class="sportout">
          <div class="left">
            <span class="oname">${o.name}</span>
            <span class="small">Kalshi <b>${o.yes_ask != null ? o.yes_ask + "¢" : "—"}</b> · model fair <b>${o.fair_pct != null ? o.fair_pct + "%" : "—"}</b>${ec != null ? ` · edge <b class="${cls}">${ec >= 0 ? "+" : ""}${ec}¢</b>` : ""}</span>
          </div>
        </div>`;
      }), "ranges");
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
  $("simMode").addEventListener("change", simModeChange);
  // MLB DFS options only apply to the sim-driven MLB path.
  const dfsMlbToggle = () => {
    const sp = $("dfsSport").value;
    const isMlb = sp === "mlb";
    const isNfl = sp === "nfl";
    const isLol = sp === "lol";
    // Portfolio + contest controls apply to every DFS sport.
    const dfs = ["mlb", "ufc", "f1", "nascar", "nfl", "lol"].includes(sp);
    if ($("dfsMlbOpts")) $("dfsMlbOpts").classList.toggle("hidden", !dfs);
    if ($("dfsMlbHint")) $("dfsMlbHint").classList.toggle("hidden", !dfs);
    // Stacking is MLB-only here (NFL stacks QB+WR automatically for GPP).
    const stackLbl = $("dfsStack") ? $("dfsStack").closest("label") : null;
    if (stackLbl) stackLbl.classList.toggle("hidden", !isMlb);
    // Manual starting-grid override is racing-only (F1/NASCAR).
    const isRacing = sp === "f1" || sp === "nascar";
    if ($("dfsGridBox")) $("dfsGridBox").classList.toggle("hidden", !isRacing);
    // NFL has a fixed positional roster (1QB/2RB/3WR/1TE/1FLEX/1DST) + a week picker.
    const wk = $("dfsNflWeek");
    if (wk) {
      wk.classList.toggle("hidden", !isNfl);
      if (isNfl && !wk.dataset.filled) {
        wk.dataset.filled = "1";
        let o = ""; for (let w = 1; w <= 18; w++) o += `<option value="${w}">Week ${w}</option>`;
        wk.innerHTML = o;
      }
    }
    const rosterLbl = $("dfsRoster") ? $("dfsRoster").closest("label") : null;
    if (rosterLbl) rosterLbl.classList.toggle("hidden", isNfl || isMlb || isLol);
    if ($("dfsMode")) $("dfsMode").classList.toggle("hidden", isNfl || isMlb || isLol);
  };
  if ($("dfsSport")) { $("dfsSport").addEventListener("change", dfsMlbToggle); dfsMlbToggle(); }
  // populate weather cities + baseball game list lazily
  try {
    const wx = await (await fetch("/api/weather/meta")).json();
    $("simWxCity").innerHTML = Object.entries(wx).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
    $("simWxCity").addEventListener("change", fillSimWxDates);
  } catch (e) {}
  $("simGameDate").value = new Date().toISOString().slice(0, 10);
  $("simGameDate").addEventListener("change", fillSimGames);
}
function simModeChange() {
  const m = $("simMode").value;
  for (const g of ["price", "game", "weather"]) $("g_" + g).classList.toggle("hidden", m !== g);
  $("g_dfs").classList.toggle("hidden", m !== "dfs");
  if (m === "game") fillSimGames();
  if (m === "weather") fillSimWxDates();
}
async function fillSimWxDates() {
  const sel = $("simWxDate"); if (!sel) return;
  try {
    const d = await (await fetch("/api/weather/" + $("simWxCity").value)).json();
    const evs = (d.events || []).filter((e) => e.model);
    sel.innerHTML = evs.map((e) => `<option value="${e.date}">${e.date}</option>`).join("")
      || `<option value="">soonest</option>`;
  } catch (e) { sel.innerHTML = `<option value="">soonest</option>`; }
}
async function fillSimGames() {
  try {
    const d = await (await fetch("/api/baseball/today?date=" + $("simGameDate").value)).json();
    $("simGameSel").innerHTML = (d.games || []).map((g) =>
      `<option value="${g.game_pk}">${g.matchup}</option>`).join("") || `<option value="">no games</option>`;
  } catch (e) {}
}
async function runGameSim() {
  const box = $("simResults");
  const pk = $("simGameSel").value;
  if (!pk) { box.innerHTML = `<div class="empty">No game selected.</div>`; return; }
  simLoader(box, "Simulating this game thousands of times…");
  try {
    const d = await (await fetch(`/api/simulate/game?date=${$("simGameDate").value}&game_pk=${pk}&sims=${simRunsValue()}`)).json();
    if (d.error) { box.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    const scores = d.top_scores.map((s) => `${s.away}-${s.home} (${s.pct}%)`).join(" · ");
    let playerBlock = "";
    const ps = d.player_sim;
    if (ps && ps.has_players) {
      const tbl = (rows) => rows.map((r) =>
        `<div class="sportout"><div class="left"><span class="oname">${r.name}</span>
          <span class="small">hits <b>${r.hits}</b> · HR <b>${r.hr}</b> · TB <b>${r.tb}</b> · SB <b>${r.sb}</b> · DK <b>${r.dk}</b></span></div></div>`).join("");
      let pitBlock = "";
      if (ps.pitchers && ps.pitchers.length) {
        const prows = ps.pitchers.map((p) => {
          const kd = p.k_dist || {};
          const dist = [4, 5, 6, 7, 8].filter((L) => kd[L] != null).map((L) => `${L}+ <b>${kd[L]}%</b>`).join(" · ");
          return `<div class="sportout"><div class="left"><span class="oname">⚾ ${p.name}</span>
            <span class="small">~<b>${p.exp_k}</b> K · ${p.avg_ip} IP · <b>${p.avg_pitches}</b> pitches before relief · bullpen ~${p.bullpen_exp_k} K</span>
            <span class="small">Ks: ${dist}</span></div></div>`;
        }).join("");
        pitBlock = `<div class="small" style="margin:10px 0 4px"><b>🎲 Starting pitchers</b> — simulated (pitch count, relief, bullpen):</div><div class="sportouts">${prows}</div>`;
      }
      playerBlock = pitBlock + `<div class="small" style="margin:10px 0 4px"><b>🎲 Player-level sim</b> (lineups posted) — expected per game, sorted by DraftKings points:</div>
        <div class="teamhdr">${d.away}</div><div class="sportouts">${tbl(ps.players.away)}</div>
        <div class="teamhdr" style="margin-top:8px">${d.home}</div><div class="sportouts">${tbl(ps.players.home)}</div>`;
    } else {
      playerBlock = `<div class="small" style="margin-top:6px;color:var(--muted)">Team-level sim (per-player detail + speed/steals appears once the lineups are posted).</div>`;
    }
    box.innerHTML = `<div class="bbgame">
      <div class="matchup">${d.matchup}</div>
      <div class="kv" style="margin-top:6px">
        <span>${d.away} win <b>${ps && ps.has_players ? ps.away_win_pct : d.away_win_pct}%</b></span>
        <span>${d.home} win <b>${ps && ps.has_players ? ps.home_win_pct : d.home_win_pct}%</b></span>
      </div>
      <div class="kv"><span>Median total <b>${d.median_total}</b> runs (10–90%: ${d.p10_total}–${d.p90_total})</span>
        <span>Blowout (5+) <b>${d.blowout_pct}%</b></span><span>Shutout <b>${d.shutout_pct}%</b></span></div>
      <div class="small" style="margin-top:6px">Most likely scores (away-home): ${scores}</div>
      ${playerBlock}
    </div>`;
  } catch (e) { box.innerHTML = `<div class="empty">Failed.</div>`; }
}
async function runWxSim() {
  const box = $("simResults");
  const th = $("simWxTh").value, dir = $("simWxDir").value;
  box.innerHTML = `<div class="empty">Simulating high temp…</div>`;
  try {
    let url = `/api/simulate/weather?city=${$("simWxCity").value}&sims=${simRunsValue()}`;
    const wd = ($("simWxDate") || {}).value;
    if (wd) url += `&date=${wd}`;
    if (th) url += `&threshold=${th}&direction=${dir}`;
    const d = await (await fetch(url)).json();
    if (d.error) { box.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    const thLine = d.prob_threshold != null
      ? `<div class="note" style="border:1px solid var(--accent);color:var(--accent)">🎯 Chance the high is ${dir} ${th}°: <b>${d.prob_threshold}%</b></div>` : "";
    box.innerHTML = `<div class="bbgame">
      <div class="matchup">${d.city} — high ${d.date}</div>
      <div class="kv" style="margin-top:6px"><span>Forecast <b>${d.forecast_high}°</b></span>
        <span>Median <b>${d.median}°</b></span><span>Likely <b>${d.p10}°–${d.p90}°</b></span><span>Range ${d.low}°–${d.high}°</span></div>
      ${thLine}</div>`;
  } catch (e) { box.innerHTML = `<div class="empty">Failed.</div>`; }
}
function renderMlbDfs(d) {
  const lev = (v) => v == null ? "" : `<span style="color:${v >= 0 ? "var(--yes)" : "var(--muted)"}">lev ${v >= 0 ? "+" : ""}${v}</span>`;
  const sharpBadge = (v) => v == null ? "" :
    `<span class="badge ${v >= 12 ? "yes" : v <= -12 ? "no" : "hold"}" style="font-size:.68rem;padding:2px 7px" title="market boom% − ownership%">⚡${v >= 0 ? "+" : ""}${v}</span>`;

  const playerRow = (p) => {
    const star = p.sim ? "🎲" : "·";
    const own = p.own != null ? `<span class="dfs-own">${p.own}%</span>` : "";
    const unconf = p.confirmed === false
      ? ` <span class="ufc-debut" title="Not confirmed in a posted lineup — projected from DraftKings' season average, not our sim. Re-run closer to first pitch.">⚠️ unconfirmed</span>` : "";
    return `<div class="dfs-prow">
      <div class="dfs-pmain"><span class="legtag">${p.pos}</span> <b>${p.name}</b>
        ${p.team ? `<span class="dfs-team">${p.team}</span>` : ""} ${sharpBadge(p.sharp)}${unconf}</div>
      <div class="dfs-pmeta">$${p.salary.toLocaleString()} · <span title="simulated">${star}</span> proj <b>${p.proj}</b> · ceil ${p.ceil} · own ${own} ${lev(p.lev)}</div>
    </div>`;
  };

  const lineupCard = (ln, i) => {
    const cs = d.contest_sim && d.contest_sim.lineups && d.contest_sim.lineups[i];
    const winTxt = cs ? (cs.win_pct >= 1 ? `${cs.win_pct}%`
      : cs.win_pct > 0 ? `~1 in ${Math.round(100 / cs.win_pct).toLocaleString()}`
      : "<1 in 1M") : "";
    const csLine = cs ? `<div class="dfs-csrow">
        <span title="chance this lineup finishes 1st in the full field">win <b>${winTxt}</b></span>
        <span title="chance you finish in the paid places">cash <b>${cs.cash_pct}%</b></span>
        <span title="modeled return on your entry fee in this contest">ROI <b class="${cs.roi_pct >= 0 ? "ev pos" : "ev neg"}">${cs.roi_pct >= 0 ? "+" : ""}${cs.roi_pct}%</b></span></div>` : "";
    const stk = ln.stack ? `<span class="dfs-chip">${ln.stack.team} ${ln.stack.n}-stack</span>` : "";
    const isBest = d.contest_sim && d.contest_sim.best_lineup_index === i;
    return `<div class="dfs-lineup${isBest ? " best" : ""}">
      <div class="dfs-lhead"><b>Lineup ${i + 1}${isBest ? " 👑" : ""}</b> ${stk}
        <span class="dfs-ltot">$${ln.salary.toLocaleString()} · proj <b>${ln.proj}</b> · ceil <b class="ev pos">${ln.ceil}</b> · own Σ ${ln.own_sum}%</span></div>
      ${csLine}
      <div class="dfs-players">${ln.players.map(playerRow).join("")}</div>
    </div>`;
  };

  // Leverage board — Vigil's edge: production the field under-rosters + the
  // betting market's read (sharp).
  let boardHtml = "";
  if (d.leverage_board && d.leverage_board.length) {
    const rows = d.leverage_board.map((p) => `<div class="dfs-brow">
      <div><b>${p.name}</b> <span class="dfs-team">${p.team || ""}</span></div>
      <div class="dfs-bmeta">$${p.salary.toLocaleString()} · proj ${p.proj} · own ${p.own ?? "–"}% · ${lev(p.lev)} ${sharpBadge(p.sharp)}</div>
    </div>`).join("");
    boardHtml = `<div class="dfs-board">
      <div class="dfs-btitle">🎯 Leverage board <span class="dfs-sub">under-owned production · ⚡ = betting market likes him more than the field (Vigil edge)</span></div>
      ${rows}</div>`;
  }

  let expHtml = "";
  if (d.n_lineups > 1 && d.exposure && d.exposure.length) {
    const chips = d.exposure.map((e) => `<span class="dfs-chip">${e.name} <b>${e.pct}%</b></span>`).join("");
    expHtml = `<details class="dfs-exp"><summary>Exposure across ${d.n_lineups} lineups</summary><div class="dfs-chips">${chips}</div></details>`;
  }

  const padWarn = d.auto_padded
    ? `<div class="dfs-note" style="border-color:#e0a23a;color:#e0a23a">⚠️ Not enough confirmed starters to fill a roster (lineups may not be posted yet), so gaps were filled from DraftKings' season averages — those players are flagged <b>⚠️ unconfirmed</b> below. Re-run closer to first pitch for a fully sim-driven lineup.</div>`
    : "";

  let csHead = "";
  if (d.contest_sim && !d.contest_sim.error) {
    const c = d.contest_sim;
    const money = (v) => "$" + Math.round(v).toLocaleString();
    csHead = `<div class="dfs-note">🏆 ${c.entries.toLocaleString()}-entry ${c.contest === "double_up" ? "double-up" : "GPP"}${c.prize_pool ? ` · ${money(c.prize_pool)} pool · ${money(c.first_prize)} to 1st · ${(c.places_paid || 0).toLocaleString()} paid` : ""} · ${money(c.entry_fee)} entry. Your lineups are scored by your <b>sims</b> setting; contest strength is gauged against a <b>${c.sample_size}</b>-lineup opponent field (${(c.iterations || 0).toLocaleString()} contest runs, scales with sims), then extrapolated to the full entry count. <i>Field, score-fit &amp; payout curve are modeled estimates.</i></div>`;
  } else if (d.contest_sim && d.contest_sim.error) {
    csHead = `<div class="dfs-note">${d.contest_sim.error}</div>`;
  }

  const un = d.unmatched && d.unmatched.length
    ? `<div class="small" style="color:var(--muted);margin-top:8px">${d.unmatched.length} CSV players had no sim/projection match (skipped): ${d.unmatched.slice(0, 6).join(", ")}${d.unmatched.length > 6 ? "…" : ""}</div>` : "";

  return `<div class="dfs-wrap">
    <div class="dfs-top">
      <div class="dfs-title">⚾ MLB DFS — ${d.objective === "ceiling" ? "GPP (ceiling)" : "Cash (median)"}${d.engine === "deep" ? ` <span class="dfs-chip" style="background:rgba(91,140,255,0.15);color:var(--accent)">deep engine</span>` : ""}</div>
      <div class="dfs-meta">${d.n_lineups} lineup${d.n_lineups > 1 ? "s" : ""} · ${d.sim_players} sim-projected players in pool of ${d.pool}</div>
    </div>
    ${padWarn}
    ${boardHtml}
    ${csHead}
    <div class="dfs-lineups">${d.lineups.map(lineupCard).join("")}</div>
    ${expHtml}
    ${un}
    <div class="small" style="margin-top:8px;color:var(--muted)">🎲 ${d.engine === "deep"
      ? "every game is played out by the deep pitch-by-pitch engine (the same one behind the 4,000-season run): pitchers face the real opposing lineup, same-game hitters are correlated for stacking, and park + weather scale the run environment"
      : "hitters come from the correlated game sim; pitchers from rate stats"}. Ownership/leverage are model estimates. Needs posted lineups (a few hours pre-game).</div>
  </div>`;
}

function renderLolDfs(d) {
  const money = (v) => "$" + Math.round(v).toLocaleString();
  const cs = d.contest_sim;
  const objName = { projection: "Cash (max projection)", ceiling: "GPP (max ceiling)", leverage: "GPP leverage" }[d.objective] || d.objective;
  const winOdds = (cs && cs.win_pct) ? Math.round(Math.min(cs.entries, 100 / cs.win_pct)) : null;
  const csHead = cs
    ? `<div class="dfs-note">🏆 ${nf(cs.entries)}-entry ${cs.contest === "double_up" ? "double-up" : "GPP"} · ${money(cs.prize_pool)} pool · ${money(cs.first_prize)} to 1st · ${cs.places_paid} paid · ${money(cs.entry_fee)} entry. <i>Modeled estimate.</i></div>
       <div class="cnums" style="margin:6px 0">
         <span>win <b>~1 in ${winOdds}</b></span><span>cash <b>${cs.cash_pct}%</b></span>
         <span>top 1% <b>${cs.top1_pct}%</b></span>
         <span>ROI <b class="${cs.roi_pct >= 0 ? "ev pos" : "ev neg"}">${cs.roi_pct >= 0 ? "+" : ""}${cs.roi_pct}%</b></span>
       </div>` : "";
  const rows = d.lineup.map((p) => {
    const own = p.own != null ? `<span class="small" style="color:var(--faint)"> · own ${p.own}%</span>` : "";
    const cptTag = p.slot === "CPT" ? ` <span class="small" style="color:var(--accent)">1.5×</span>` : "";
    // Show ours/DK when the model blended in; else just the (base×1.5) for CPT.
    let base = "";
    if (p.our_proj != null) base = `<span class="small" style="color:var(--muted)"> (ours ${p.our_proj} / DK ${p.dk_proj})</span>`;
    else if (p.slot === "CPT") base = `<span class="small" style="color:var(--muted)"> (${p.base_proj}×1.5)</span>`;
    return `<div class="nfl-dfsrow">
      <span class="nfl-dfsslot">${p.slot}${cptTag}</span>
      <span class="nfl-dfsname">${p.name} <span class="small" style="color:var(--muted)">${p.role}${p.team ? " · " + p.team : ""}</span></span>
      <span class="nfl-dfsnum">$${nf(p.salary)}</span>
      <span class="nfl-dfsnum">proj <b>${p.proj}</b>${base}</span>
      <span class="nfl-dfsnum">${own}</span>
    </div>`;
  }).join("");
  return `<div class="combo hl prop">
    <div class="chead"><span class="ctag">🎮 LoL lineup — ${objName}</span></div>
    <div class="cnums" style="margin:4px 0">
      <span>salary <b>$${nf(d.salary)}</b>/$${nf(d.cap)}</span>
      <span>proj <b>${d.proj}</b></span>
      <span>floor ${d.floor}</span><span>median ${d.median}</span>
      <span>ceiling <b class="ev pos">${d.ceiling}</b></span>
    </div>
    ${csHead}
    <div class="nfl-dfslist">${rows}</div>
    <div class="small" style="margin-top:6px;color:var(--muted)">${d.note}</div>
  </div>`;
}

function renderNflDfs(d) {
  const money = (v) => "$" + Math.round(v).toLocaleString();
  const cs = d.contest_sim;
  const objName = { projection: "Cash (max projection)", ceiling: "GPP (max ceiling)", leverage: "GPP leverage" }[d.objective] || d.objective;
  const csHead = cs
    ? `<div class="dfs-note">🏆 ${nf(cs.entries)}-entry ${cs.contest === "double_up" ? "double-up" : "GPP"} · ${money(cs.prize_pool)} pool · ${money(cs.first_prize)} to 1st · ${cs.places_paid} paid · ${money(cs.entry_fee)} entry. <i>Modeled estimate.</i></div>
       <div class="cnums" style="margin:6px 0">
         <span>win <b>~1 in ${cs.win_pct > 0 ? Math.round(100 / cs.win_pct) : "∞"}</b></span>
         <span>cash <b>${cs.cash_pct}%</b></span>
         <span>top 1% <b>${cs.top1_pct}%</b></span>
         <span>ROI <b class="${cs.roi_pct >= 0 ? "ev pos" : "ev neg"}">${cs.roi_pct >= 0 ? "+" : ""}${cs.roi_pct}%</b></span>
       </div>` : "";
  const rows = d.lineup.map((p) => {
    const own = p.own != null ? `<span class="small" style="color:var(--faint)"> · own ${p.own}%</span>` : "";
    return `<div class="nfl-dfsrow">
      <span class="nfl-dfsslot">${p.slot}</span>
      <span class="nfl-dfsname">${p.name} <span class="small" style="color:var(--muted)">${p.pos}${p.team ? " · " + p.team : ""}</span></span>
      <span class="nfl-dfsnum">$${nf(p.salary)}</span>
      <span class="nfl-dfsnum">proj <b>${p.proj}</b></span>
      <span class="nfl-dfsnum">ceil <b class="ev pos">${p.ceiling}</b>${own}</span>
    </div>`;
  }).join("");
  const un = (d.unmatched && d.unmatched.length)
    ? `<div class="small" style="color:var(--muted);margin-top:6px">${d.unmatched.length} player(s) not in the Sleeper projection — used the CSV's own number (no correlation): ${d.unmatched.slice(0, 6).join(", ")}${d.unmatched.length > 6 ? "…" : ""}</div>` : "";
  return `<div class="combo hl prop">
    <div class="chead"><span class="ctag">🏈 NFL lineup — ${objName}</span>
      <span class="small">Week ${d.week} · ${d.stack ? "QB stack" : "no stack"}</span></div>
    <div class="cnums" style="margin:4px 0">
      <span>salary <b>$${nf(d.salary)}</b>/$${nf(d.cap)}</span>
      <span>proj <b>${d.proj}</b></span>
      <span>floor ${d.floor}</span><span>median ${d.median}</span>
      <span>ceiling <b class="ev pos">${d.ceiling}</b></span>
    </div>
    ${csHead}
    <div class="nfl-dfslist">${rows}</div>
    ${un}
    <div class="small" style="margin-top:6px;color:var(--muted)">${d.note}</div>
  </div>`;
}

async function runDfsSim() {
  const box = $("simResults");
  const csv = $("dfsCsv").value;
  if (!csv.trim()) { box.innerHTML = `<div class="empty">Paste your DraftKings salaries CSV first.</div>`; return; }
  box.innerHTML = `<div class="empty">Optimizing + simulating lineup…</div>`;
  try {
    const d = await (await fetch("/api/simulate/dfs", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ csv, sport: $("dfsSport").value, roster: parseInt($("dfsRoster").value, 10),
        cap: parseInt($("dfsCap").value, 10), mode: $("dfsMode").value, objective: $("dfsObjective").value,
        sims: simRunsValue(),
        lineups: parseInt(($("dfsLineups") || {}).value, 10) || 1,
        max_exposure: parseFloat(($("dfsMaxExp") || {}).value) || 60,
        stack_min: parseInt(($("dfsStack") || {}).value, 10) || 0,
        min_uniq: parseInt(($("dfsUniq") || {}).value, 10) || 2,
        contest: ($("dfsContest") || {}).value || null,
        field_size: parseInt(($("dfsField") || {}).value, 10) || 600,
        contest_size: parseInt(($("dfsEntries") || {}).value, 10) || 0,
        entry_fee: parseFloat(($("dfsEntry") || {}).value) || 1,
        prize_pool: parseFloat(($("dfsPool") || {}).value) || 0,
        first_prize: parseFloat(($("dfsFirst") || {}).value) || 0,
        grid: (($("dfsGrid") || {}).value || "").trim() || null,
        week: parseInt(($("dfsNflWeek") || {}).value, 10) || 1 }),
    })).json();
    if (d.error === "upgrade_required") { box.innerHTML = upgradeNote(d); return; }
    if (d.error) { box.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    if (d.total_ceil != null) { box.innerHTML = renderMlbDfs(d); return; }   // sim-driven MLB
    if (d.is_lol) { box.innerHTML = renderLolDfs(d); return; }
    if (d.lineup && d.lineup[0] && d.lineup[0].slot) { box.innerHTML = renderNflDfs(d); return; }
    const dfsRow = (p) => {
      const startTag = p.start != null ? `<span class="legtag">P${p.start}</span> ` : "";
      let pd = "";
      if (p.pd_adj != null && Math.abs(p.pd_adj) >= 0.1) {
        const cls = p.pd_adj > 0 ? "#3ad17a" : "#e0566a";
        pd = ` <span style="color:${cls}">(${p.pd_adj > 0 ? "+" : ""}${p.pd_adj} PD, was ${p.base_proj})</span>`;
      }
      let ufcBits = "";
      if (p.rating != null || p.win_pct != null) {
        const parts = [];
        if (p.rating != null) {
          const rc = p.rating >= 60 ? "#3ad17a" : p.rating <= 40 ? "#e0566a" : "var(--muted)";
          parts.push(`rating <b style="color:${rc}">${p.rating}</b>`);
        }
        if (p.win_pct != null) parts.push(`win ${p.win_pct}%`);
        if (p.ceil_proj != null) parts.push(`ceil ${p.ceil_proj}`);
        if (p.fights > 0 && p.record) parts.push(`UFC ${p.record}`);
        else if (p.career_record) parts.push(`pro ${p.career_record}`);
        ufcBits = ` <span class="small" style="color:var(--muted)">· ${parts.join(" · ")}</span>`;
        if (p.debut) ufcBits += ` <span class="ufc-debut" title="UFC debut — striking/grappling are league-average; finishing & durability seeded from the pro record">⚠️ UFC debut (pro ${p.career_record})</span>`;
        else if (p.defaulted) ufcBits += ` <span class="ufc-debut" title="No fight history at all — pure league-average placeholder">⚠️ no data</span>`;
        else if (p.thin) ufcBits += ` <span class="ufc-debut" title="Few UFC fights — rating shrunk toward league average">⚠️ thin</span>`;
      }
      const own = p.own != null ? ` · <span title="projected field ownership">own ${p.own}%</span>` : "";
      return `<div class="sportout"><div class="left"><span class="oname">${startTag}${p.captain ? "⭐ " : ""}${p.name}${p.captain ? " (CPT 1.5×)" : ""}</span><span class="small">$${p.salary.toLocaleString()} · proj ${p.proj}${own}${pd}${ufcBits}</span></div></div>`;
    };
    let gridBanner = "";
    const g = d.grid;
    if (g && g.available) {
      const un = g.unmatched && g.unmatched.length
        ? ` · <span style="color:var(--muted)">${g.unmatched.length} unmatched (no grid adj)</span>` : "";
      const basis = g.sim_used
        ? `expected finish from the <b>race simulator</b> (${g.sim_drivers} drivers)`
        : (g.form_used ? "recent form" : "salary-deserved finish");
      const gridName = g.manual ? `<b>your pasted starting grid</b>` : `<b>${g.race}</b> (${g.series}, ${g.field}-car field)`;
      gridBanner = `<div class="small" style="margin:4px 0 0">🏁 Grid: ${gridName} — ${g.matched} drivers matched${un}. Place differential off the ${g.manual ? "pasted starting order" : "actual qualifying order"}, using ${basis}.${g.sim_used ? "" : " <span style=\"color:var(--muted)\">(simulator warming up — rerun in ~1 min for sim-driven finishes)</span>"}</div>`;
    } else if (g && !g.available) {
      gridBanner = `<div class="small" style="margin:4px 0 0">🏁 ${g.reason} — using season points only (no place-differential adjustment yet).</div>`;
    }
    const u = d.ufc;
    if (u && u.available) {
      const mode = d.objective === "ceiling"
        ? `<b style="color:#3ad17a">GPP / ceiling</b> — optimizing each fighter's 90th-pct (boom) night, so finishers with knockout upside are favored`
        : `<b>Cash / projection</b> — optimizing mean points for a steady floor`;
      gridBanner = `<div class="small" style="margin:4px 0 0">🥊 <b>${u.event}</b> — ${u.matched} fighters projected by our <b>fight simulator</b> (ratings built from each fighter's past-fight history → win prob + method/round → DK points). Mode: ${mode}.</div>`;
    } else if (u && !u.available) {
      gridBanner = `<div class="small" style="margin:4px 0 0">🥊 ${u.reason}.</div>`;
    }
    // Contest simulation banner (win% / cash% / ROI at the real field size).
    let csBanner = "";
    const cs = d.contest_sim;
    if (cs && !cs.error) {
      const money = (v) => "$" + Math.round(v).toLocaleString();
      csBanner = `<div class="dfs-note" style="margin-top:6px">🏆 ${cs.entries.toLocaleString()}-entry ${cs.contest === "double_up" ? "double-up" : "GPP"}${cs.prize_pool ? ` · ${money(cs.prize_pool)} pool · ${money(cs.first_prize)} to 1st · ${(cs.places_paid || 0).toLocaleString()} paid` : ""} · ${money(cs.entry_fee)} entry. Strength gauged vs a ${cs.sample_size}-lineup opponent field (scales with your sims setting), extrapolated to the full contest. <i>Modeled estimate.</i></div>`;
    } else if (cs && cs.error) {
      csBanner = `<div class="dfs-note" style="margin-top:6px">${cs.error}</div>`;
    }
    const lineups = d.lineups && d.lineups.length ? d.lineups : [{ lineup: d.lineup, total_salary: d.total_salary, total_proj: d.total_proj, own_sum: null, sim: d.sim }];
    const csRow = (i) => {
      const l = cs && !cs.error && cs.lineups && cs.lineups[i];
      if (!l) return "";
      const winTxt = l.win_pct >= 1 ? `${l.win_pct}%` : l.win_pct > 0 ? `~1 in ${Math.round(100 / l.win_pct).toLocaleString()}` : "<1 in 1M";
      return `<div class="dfs-csrow"><span title="chance this lineup wins the whole field">win <b>${winTxt}</b></span><span>cash <b>${l.cash_pct}%</b></span><span>ROI <b class="${l.roi_pct >= 0 ? "ev pos" : "ev neg"}">${l.roi_pct >= 0 ? "+" : ""}${l.roi_pct}%</b></span></div>`;
    };
    const lineupCards = lineups.map((L, i) => {
      const best = cs && !cs.error && cs.best_lineup_index === i;
      return `<div class="dfs-lineup${best ? " best" : ""}">
        ${lineups.length > 1 ? `<div class="dfs-lhead"><b>Lineup ${i + 1}${best ? " 👑" : ""}</b> <span class="dfs-ltot">$${L.total_salary.toLocaleString()} · proj <b>${L.total_proj}</b>${L.own_sum != null ? ` · own Σ ${L.own_sum}%` : ""} · 🟢 ceil ${L.sim.ceiling}</span></div>` : ""}
        ${csRow(i)}
        <div class="sportouts" style="margin-top:6px">${L.lineup.map(dfsRow).join("")}</div>
      </div>`;
    }).join("");
    let expHtml = "";
    if (d.exposure && d.exposure.length) {
      const chips = d.exposure.map((e) => `<span class="dfs-chip">${e.name.split(" ").slice(-1)[0]} <b>${e.pct}%</b></span>`).join("");
      expHtml = `<details class="dfs-exp"><summary>Exposure across ${d.n_lineups} lineups</summary><div class="dfs-chips">${chips}</div></details>`;
    }
    box.innerHTML = `<div class="bbgame">
      <div class="matchup">${lineups.length > 1 ? `${lineups.length}-lineup portfolio` : `Optimal ${d.roster}-player lineup`} (${d.pool} in pool)</div>
      ${gridBanner}
      ${csBanner}
      ${lineups.length === 1 ? `<div class="kv" style="margin-top:6px"><span>Salary <b>$${lineups[0].total_salary.toLocaleString()}</b> / $${d.cap.toLocaleString()}</span><span>Projected <b>${lineups[0].total_proj}</b> pts</span></div>
      <div class="kv"><span>🔴 Floor <b>${d.sim.floor}</b></span><span>Median <b>${d.sim.median}</b></span><span>🟢 Ceiling <b class="ev pos">${d.sim.ceiling}</b></span><span>Max <b>${d.sim.max}</b></span></div>` : ""}
      <div class="dfs-lineups" style="margin-top:8px">${lineupCards}</div>
      ${expHtml}
      <div class="small" style="margin-top:6px">Floor/ceiling are the 10th/90th-pct totals over the SAME simulated races/fights (correlated — one winner per race, wrecks take out several cars at once, one fighter per bout banks the win bonus).${g && g.available ? " <b>PD</b> = place-differential adjustment." : ""} Ownership is a model estimate of what the field rosters.</div>
    </div>`;
  } catch (e) { box.innerHTML = `<div class="empty">Failed.</div>`; }
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
  const mode = $("simMode").value;
  if (mode === "game") return runGameSim();
  if (mode === "weather") return runWxSim();
  if (mode === "dfs") return runDfsSim();
  const box = $("simResults");
  const kind = $("simKind").value, key = $("simKey").value, horizon = $("simHorizon").value;
  const th = $("simThreshold").value, dir = $("simDir").value;
  box.innerHTML = `<div class="empty">Running ${simRunsValue().toLocaleString()} paths…</div>`;
  try {
    let url = `/api/simulate/price?kind=${kind}&key=${key}&horizon=${horizon}&sims=${simRunsValue()}`;
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
  // Default: nothing checked -- the user picks which sports to combine.
  $("combineCats").innerHTML = Object.entries(meta).map(([k, v]) =>
    `<label><input type="checkbox" value="${k}"/> ${v}</label>`
  ).join("");
  combineCatsLoaded = true;
}
async function buildRecommended() {
  const cats = [...document.querySelectorAll("#combineCats input:checked")].map((i) => i.value);
  const out = $("cmbRecOut");
  if (!cats.length) { out.innerHTML = `<div class="empty">Check one or more sports above first.</div>`; return; }
  const date = ($("bbDate") && $("bbDate").value) || new Date().toISOString().slice(0, 10);
  out.innerHTML = `<div class="empty">Building the best combos across ${cats.length} sport${cats.length > 1 ? "s" : ""}…</div>`;
  try {
    const d = await (await fetch(`/api/combine/recommended?cats=${cats.join(",")}&date=${date}`)).json();
    if (d.error === "no_cats") { out.innerHTML = `<div class="empty">Check one or more sports above first.</div>`; return; }
    if (d.error) { out.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    let html = "";
    if (d.counts && Object.keys(d.counts).length)
      html += `<div class="small" style="margin-bottom:8px">Legs available: ${Object.entries(d.counts).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>`;
    if (d.same_game_only)
      html += `<div class="small" style="margin-bottom:8px;color:var(--muted)">Only one game on the slate today, so these are <b>same-game</b> parlays — legs from that game are correlated, priced with the correlation-aware sim (not a naive product).</div>`;
    const seen = new Set();
    const blocks = [
      ["best", "⭐ Best (all-around)", "hl value"],
      ["safest", "🛡️ Safest", "hl"],
      ["best_value", "💰 Best value (+EV)", "hl prop"],
    ];
    let any = false;
    for (const [key, title, cls] of blocks) {
      const c = d[key];
      if (!c) continue;
      const sig = JSON.stringify(c.legs);
      if (seen.has(sig)) continue;          // skip duplicates (e.g. best == best_value)
      seen.add(sig);
      const ev = c.ev_pct != null ? ` · EV <b class="${c.ev_pct >= 0 ? "pos" : "neg"}">${c.ev_pct >= 0 ? "+" : ""}${c.ev_pct}%</b>` : "";
      const edge = c.total_edge_cents ? ` · total edge ${c.total_edge_cents > 0 ? "+" : ""}${c.total_edge_cents}¢` : "";
      html += renderCombo(c, title, cls);
      const why = (c.reasons && c.reasons.length)
        ? `<div class="small" style="margin:2px 0 0">${c.reasons.map((r) => `<div>${r}</div>`).join("")}</div>` : "";
      html += `<div class="small" style="margin:-4px 0 10px">${c.n_legs} legs · ~${c.combined_prob_pct}% to cash · ${c.fair_payout_x}× fair${ev}${edge}${why}</div>`;
      any = true;
    }
    if (!any) html += `<div class="empty">Not enough legs to build a combo from those sports right now. Try checking more sports.</div>`;
    out.innerHTML = html;
  } catch (e) {
    out.innerHTML = `<div class="empty">Build failed — try again.</div>`;
  }
}
async function buildCombine() {
  const cats = [...document.querySelectorAll("#combineCats input:checked")].map((i) => i.value);
  const out = $("combineOut");
  if (!cats.length) { out.innerHTML = `<div class="empty">Pick at least one category.</div>`; return; }
  const n = parseInt($("cmbN").value, 10) || 4;
  const t = parseInt($("cmbTarget").value, 10) || 65;
  const p = parseFloat($("cmbPayout").value) || 0;
  const legsMode = ($("cmbLegsMode") || {}).value || "prefer";
  const payoutMode = ($("cmbPayoutMode") || {}).value || "off";
  const conn = ($("cmbConn") || {}).value || "or";
  const date = ($("bbDate") && $("bbDate").value) || new Date().toISOString().slice(0, 10);
  out.innerHTML = `<div class="empty">Gathering legs across ${cats.length} categories… (a few seconds)</div>`;
  try {
    const q = `cats=${cats.join(",")}&legs=${n}&target=${t}&payout=${p}&date=${date}`
      + `&legs_mode=${legsMode}&payout_mode=${payoutMode}&conn=${conn}`;
    const d = await (await fetch(`/api/combine?${q}`)).json();
    if (d.error) { out.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    let html = "";
    if (d.counts && Object.keys(d.counts).length)
      html += `<div class="small" style="margin-bottom:8px">Legs available: ${Object.entries(d.counts).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>`;
    if (d.combo) {
      const c = d.combo;
      const wantPayout = payoutMode !== "off" && p > 1;
      const title = `🎰 ${c.legs_used || c.n_legs}-leg mega parlay → ${c.fair_payout_x}× (every leg ≥ ${t}%)`;
      let note = "";
      if (c.expanded && c.legs_used !== c.requested_legs)
        note += `<div class="small">Used <b>${c.legs_used}</b> legs (you set ${c.requested_legs}) to best fit your targets while keeping every leg ≥ ${t}%.</div>`;
      if (c.legs_met === false && legsMode === "require")
        note += `<div class="small">⚠️ Couldn't field exactly ${c.legs_target} legs ≥ ${t}% — showing the closest (${c.legs_used}).</div>`;
      if (wantPayout && c.payout_reached === false)
        note += `<div class="small">⚠️ Couldn't reach ${p}× with every leg ≥ ${t}% — the best at that floor is <b>${c.fair_payout_x}×</b>. Lower the floor, drop the leg-count requirement, or add categories.</div>`;
      if (c.hard_ok === false)
        note += `<div class="small" style="color:#e0566a">⚠️ Your required target(s) couldn't both be met${conn === "and" ? " (AND)" : ""} — showing the closest parlay. Try switching AND→OR or relaxing one target to <b>recommend</b>.</div>`;
      note += `<div class="small">At ${c.fair_payout_x}× the chance is ~<b>${c.combined_prob_pct}%</b> (≈1 in ${Math.round(c.fair_payout_x)}). ${c.legs_meeting_target}/${c.legs_used} legs meet the ${t}% target.</div>`;
      html += renderCombo(c, title, "hl prop") + note;
      html += comboSimControl(c);
    } else {
      html += `<div class="empty">No legs available for those categories right now.</div>`;
    }
    out.innerHTML = html;
  } catch (e) {
    out.innerHTML = `<div class="empty">Build failed — try again.</div>`;
  }
}

// ---- Prop value finder (Kalshi price vs recent form) ----------------------
async function loadValue() {
  const box = $("bbValue");
  if (!box) return;
  const g = ($("valGames") || {}).value || 15;
  const e = ($("valEdge") || {}).value || 10;
  box.innerHTML = `<div class="empty">Scanning Kalshi props vs recent game logs…</div>`;
  try {
    const d = await (await fetch(`/api/baseball/value?games=${g}&edge=${e}`)).json();
    if (d.error === "upgrade_required") { box.innerHTML = upgradeNote(d); return; }
    if (d.error) { box.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    if (!d.plays || !d.plays.length) {
      box.innerHTML = `<div class="empty">No props with a ≥${e}¢ edge right now (${d.markets_scanned} scanned). Lower the edge or check back closer to game time.</div>`;
      return;
    }
    const rows = d.plays.map((p) => {
      const streak = p.streak ? ` <span style="color:var(--hold)">⚠️ streak</span>` : "";
      const seas = p.season_pct != null ? ` <span style="color:var(--muted)">(season ${p.season_pct}%)</span>` : "";
      return `<div class="scanrow edge">
        <div class="scanhead"><div class="strike">${p.label}</div>
          <div class="small">Kalshi ${p.side} <b>${p.cost_cents}¢</b> · recent <b>${p.recent_pct}%</b> in ${p.games}g${seas}${streak}</div></div>
        <div class="actionline"><span class="edgeval pos">+${p.edge_cents}¢ edge</span>
          <span class="plain">✅ Buy <b>${p.side}</b> @ <b>${p.cost_cents}¢</b> — model from recent form ${p.recent_pct}%</span></div>
      </div>`;
    }).join("");
    box.innerHTML = `<div class="small" style="margin-bottom:6px">${d.plays.length} value plays from ${d.markets_scanned} Kalshi player-prop markets (last ${d.n_games} games):</div>` + rows;
  } catch (e2) {
    box.innerHTML = `<div class="empty">Scan failed — try again.</div>`;
  }
}

// ---- Baseball model track record ------------------------------------------
async function loadBaseballRecord() {
  if (!isOwner()) return;   // model track record is owner-only data
  try {
    const r = await (await fetch("/api/baseball/record")).json();
    const el = $("bbRecord");
    if (!el) return;
    if (!r.graded) {
      el.innerHTML = r.pending ? `<span>Model track record: ${r.pending} picks awaiting results…</span>` : "";
      return;
    }
    const pct = (v) => (v >= 0 ? "+" : "") + v + "%";
    const briefBrier = r.brier != null
      ? ` · Brier <b style="color:${r.brier < 0.25 ? "#3ad17a" : "#e0566a"}">${r.brier}</b><span style="color:var(--muted)">/${r.brier_baseline}</span>` : "";
    // Headline: W-L is noisy at small n; the lines below are what actually matter.
    let html = `Model record: <b>${r.wins}-${r.losses}</b> (${r.accuracy_pct}%)` +
      (r.roi_pct != null ? ` · ROI <b class="${r.roi_pct >= 0 ? "ev pos" : "ev neg"}">${pct(r.roi_pct)}</b>` : "") +
      briefBrier + (r.pending ? ` · ${r.pending} pending` : "");
    // The real tests: edge-filtered ROI (bets you'd actually place) + CLV.
    const extra = [];
    if (r.roi_edge_pct != null)
      extra.push(`Edge bets (≥${r.edge_threshold}¢): <b class="${r.roi_edge_pct >= 0 ? "ev pos" : "ev neg"}">${pct(r.roi_edge_pct)}</b> ROI <span style="color:var(--muted)">(${r.edge_bets})</span>`);
    if (r.clv_avg != null)
      extra.push(`CLV <b class="${r.clv_avg >= 0 ? "ev pos" : "ev neg"}">${r.clv_avg >= 0 ? "+" : ""}${r.clv_avg}¢</b> <span style="color:var(--muted)">(${r.clv_positive_pct}% beat close, ${r.clv_n})</span>`);
    if (r.totals_accuracy) {
      const t = r.totals_accuracy;
      extra.push(`Sim totals: predicted <b>${t.predicted_avg}</b> vs actual <b>${t.actual_avg}</b> (off by <b>${t.mean_abs_error}</b> runs avg, bias ${t.bias >= 0 ? "+" : ""}${t.bias}, ${t.n} games)`);
    }
    if (r.calibration && r.calibration.length)
      extra.push("Calibration " + r.calibration.map((b) =>
        `${b.range}: ${b.predicted}→<b style="color:${Math.abs(b.actual - b.predicted) <= 8 ? "#3ad17a" : "var(--muted)"}">${b.actual}%</b>`).join(" · "));
    // Blend components graded separately: does the deep engine earn its weight?
    const ms = r.model_split;
    if (ms && (ms.factor || ms.deep)) {
      const f = (x, nm) => x ? `${nm} <b>${x.acc_pct}%</b> <span style="color:var(--muted)">(Brier ${x.brier}, ${x.n})</span>` : `${nm} —`;
      extra.push(`🧬 Split: ${f(ms.factor, "factor")} · ${f(ms.deep, "deep")} · ${f(ms.blend, "blend")} — the blend weight auto-tunes on this once 40+ games carry both.`);
    }
    // Live calibration: temperature reining in high-end overconfidence, fit on
    // this record. T=1.00 = no correction yet (too little data); >1 = active.
    const ct = r.calibration_temps;
    if (ct && (ct.win_t > 1.001 || ct.prop_t > 1.001)) {
      const tt = (t, n) => `<b>${(+t).toFixed(2)}×</b> <span style="color:var(--muted)">(${n})</span>`;
      extra.push(`🎯 Calibrating overconfidence: win ${tt(ct.win_t, ct.win_n)} · props ${tt(ct.prop_t, ct.prop_n)} — high-confidence picks pulled toward their real hit-rate (auto-fit on this record).`);
    }
    if (extra.length)
      html += `<div class="small" style="margin-top:3px">${extra.join(" &nbsp;·&nbsp; ")}</div>`;
    if (r.graded < 50)
      html += `<div class="small" style="color:var(--muted);margin-top:2px">⚠️ Only ${r.graded} graded — too few to judge; W-L is mostly noise until ~100+. Watch Brier (&lt;0.25 = real signal) and CLV.</div>`;
    el.innerHTML = html;
  } catch (e) { /* ignore */ }
}

// ---- Prop recorder track record -------------------------------------------
async function loadPropLog() {
  if (!isOwner()) return;   // prop recorder log is owner-only data
  const el = $("bbPropLog");
  if (!el) return;
  el.innerHTML = `<div class="empty">Loading the prop track record…</div>`;
  try {
    const r = await (await fetch("/api/baseball/proplog")).json();
    if (r.error) { el.innerHTML = `<div class="empty">${r.error}</div>`; return; }
    const rec = r.recorder || {};
    if (!r.graded) {
      el.innerHTML = `<div class="empty">${rec.logged || 0} props logged, ${r.pending || 0} awaiting results. Keep the app open — the recorder logs each game's props and grades them once finals post.</div>`;
      return;
    }
    const briercell = (v, n) => v == null ? "—" :
      `<b style="color:${v < 0.25 ? "#3ad17a" : "#e0566a"}">${v}</b>`;
    const roiline = (label, o) => {
      if (!o) return `${label}: <span style="color:var(--muted)">no qualifying bets yet</span>`;
      const cls = o.roi_pct >= 0 ? "ev pos" : "ev neg";
      return `${label}: <b class="${cls}">${o.roi_pct >= 0 ? "+" : ""}${o.roi_pct}%</b> ROI · ${o.win_pct}% W <span style="color:var(--muted)">(${o.bets} bets, ${o.pnl_per_contract_c >= 0 ? "+" : ""}${o.pnl_per_contract_c}¢/contract)</span>`;
    };
    let html = `Graded <b>${r.graded}</b> props (${r.hit_rate_pct}% hit the line) · ${r.pending} pending`;
    html += `<div class="small" style="margin-top:4px">Brier (lower=better, 0.25=coin flip): ` +
      `model ${briercell(r.model_brier)} · recent-form ${briercell(r.recent_brier)} · Kalshi price ${briercell(r.market_brier)} <span style="color:var(--muted)">(n=${r.brier_n})</span></div>`;
    html += `<div class="small" style="margin-top:3px">${roiline("Bet model edge ≥" + r.min_edge + "¢", r.model_edge_roi)}</div>`;
    html += `<div class="small" style="margin-top:2px">${roiline("Bet recent-form edge ≥" + r.min_edge + "¢", r.recent_edge_roi)}</div>`;
    if (r.calibration && r.calibration.length)
      html += `<div class="small" style="margin-top:3px">Model calibration: ` + r.calibration.map((b) =>
        `${b.range}: ${b.predicted}→<b style="color:${Math.abs(b.actual - b.predicted) <= 8 ? "#3ad17a" : "var(--muted)"}">${b.actual}%</b> <span style="color:var(--muted)">(${b.n})</span>`).join(" · ") + `</div>`;
    const clv = r.clv || {};
    if (clv.picks) {
      const cls = clv.avg_clv_cents >= 0 ? "ev pos" : "ev neg";
      html += `<div class="small" style="margin-top:6px" title="Closing-line value: for props the model flagged at first sight, did Kalshi's CLOSING price move toward our number? Beating the close consistently proves edge independent of win/loss luck.">📐 <b>CLV</b> (beat-the-close): <b class="${cls}">${clv.avg_clv_cents >= 0 ? "+" : ""}${clv.avg_clv_cents}¢</b> avg · beat close <b>${clv.beat_close_pct}%</b>${clv.push_pct ? ` (push ${clv.push_pct}%)` : ""} · won ${clv.win_pct}% <span style="color:var(--muted)">(${clv.picks} flagged picks)</span></div>`;
      if (clv.picks < 50)
        html += `<div class="small" style="color:var(--muted)">CLV needs ~50+ picks to mean much — it accumulates as the recorder runs.</div>`;
    } else {
      html += `<div class="small" style="color:var(--muted);margin-top:6px">📐 CLV (beat-the-close) starts tracking from today's props — the recorder now snapshots each prop's ENTRY price and compares it to the close.</div>`;
    }
    if (r.graded < 100)
      html += `<div class="small" style="color:var(--muted);margin-top:3px">⚠️ Only ${r.graded} graded — too few to trust the ROI; watch the Brier scores converge first (~100+ needed).</div>`;
    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = `<div class="empty">Couldn't load the prop record — try again.</div>`;
  }
}

// ---- Wire up --------------------------------------------------------------
async function init() {
  // Subscription tier (cookie-backed; the server enforces gating).
  try { TIERMATRIX = await (await fetch("/api/tiers")).json(); }
  catch (e) { TIERMATRIX = { feature_min: {}, tiers: { free: { max_sims: 1000, price: "$0" } } }; }
  if ($("tierSel")) {
    // Reflect the SELECTED tier (so the owner can preview the customer view),
    // not the server-resolved one (which is always 'owner' until enforced).
    $("tierSel").value = localStorage.getItem("tier") || "owner";
    $("tierSel").addEventListener("change", () => { setTier($("tierSel").value); location.reload(); });
  }
  applyTierUI();

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
  if ($("scanBuysOnly")) $("scanBuysOnly").addEventListener("change", runScan);

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
  // Best Bets is the landing tab — kick off its scan without waiting for a click.
  if ($("tab-bestbets") && !$("tab-bestbets").classList.contains("hidden")) {
    $("bbetsResults").dataset.loaded = "1";
    loadBestBets();
  }
  $("bbDate").value = new Date().toISOString().slice(0, 10);
  $("bbBtn").addEventListener("click", () => loadBaseball());
  $("bbRefresh").addEventListener("click", () => loadBaseball(true));
  if ($("valBtn")) $("valBtn").addEventListener("click", loadValue);
  if ($("propLogBtn")) $("propLogBtn").addEventListener("click", loadPropLog);

  // Sports setup
  $("sportBtn").addEventListener("click", loadSports);
  $("sportSel").addEventListener("change", loadSports);
  $("sportHideThin").addEventListener("change", renderSports);

  // Weather setup
  $("wxBtn").addEventListener("click", loadWeather);
  $("wxCity").addEventListener("change", loadWeather);
  if ($("wxDate")) $("wxDate").addEventListener("change", loadWeather);

  // Commodities
  $("comBtn").addEventListener("click", loadCommodities);
  $("comSel").addEventListener("change", loadCommodities);

  // Best-ball team grader (multi-team)
  if ($("gradeTeams")) {
    loadGradeTeams();                          // restore saved teams (else a blank one)
    $("gradeAddBtn").addEventListener("click", () => addGradeTeam());
    $("gradeMineBtn").addEventListener("click", gradeAddMine);
    $("gradeSaveBtn").addEventListener("click", () => saveGradeTeams(false));
    $("gradeRunBtn").addEventListener("click", gradeRunAll);
    $("aiKeySave").addEventListener("click", () => saveAiKey($("aiKeyInput").value));
    $("aiKeyClear").addEventListener("click", () => saveAiKey(""));
    refreshAiKeyStatus();
  }

  // Mega combo maker
  $("cmbBtn").addEventListener("click", buildCombine);
  $("cmbRecBtn").addEventListener("click", buildRecommended);
  document.querySelectorAll("#cmbSubtabs .subtab").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll("#cmbSubtabs .subtab").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      const rec = b.dataset.cmbsub === "rec";
      $("cmbRecView").classList.toggle("hidden", !rec);
      $("cmbMakerView").classList.toggle("hidden", rec);
      $("combineOut").classList.toggle("hidden", rec);
    });
  });

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

// ---- Auto-update watcher --------------------------------------------------
// Poll the server's build token; when it changes (a push went live), show a
// one-tap "refresh for the update" banner. Combined with the server-side
// self-updater, a push reaches the phone with nothing to do but tap.
let _buildToken = null;
async function _checkForUpdate() {
  try {
    const d = await (await fetch("/api/version", { cache: "no-store" })).json();
    if (!d || !d.v) return;
    if (_buildToken == null) { _buildToken = d.v; return; }   // first read = baseline
    if (d.v !== _buildToken && !$("updateBanner")) _showUpdateBanner();
  } catch (e) { /* offline / server restarting — ignore */ }
}
function _showUpdateBanner() {
  const bar = document.createElement("div");
  bar.id = "updateBanner";
  bar.innerHTML = `🔄 <b>New version available</b> <button id="updateBtn">Refresh</button>`;
  document.body.appendChild(bar);
  $("updateBtn").addEventListener("click", () => location.reload(true));
}
setInterval(_checkForUpdate, 60000);   // check every minute
_checkForUpdate();

// Register the service worker (PWA / installable). Only works on a secure
// context (https or localhost); silently skips otherwise.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}
