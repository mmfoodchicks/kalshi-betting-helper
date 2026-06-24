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
  return `${sp.name} (${hand}) — <b>${sp.era}</b> ERA${fip}, <b>${sp.whip}</b> WHIP, ${sp.ip} IP${recent}`;
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
    const rp = w.run_pct;
    const runTag = (rp != null && Math.abs(rp) >= 0.3)
      ? ` → <b class="${rp > 0 ? "ev pos" : "ev neg"}">${rp > 0 ? "+" : ""}${rp}% runs</b>` : "";
    wxLine = `<div class="small">🌤️ ${w.stadium}: <b>${w.temp_f}°F</b>, wind ${w.wind_mph}mph ${w.wind_dir} — ${w.wind_effect}${runTag}${w.precip_pct ? ` · ${w.precip_pct}% precip` : ""}${w.summary ? ` · ${w.summary}` : ""} <span style="color:var(--border)">[${w.source}]</span></div>`;
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

// Unified combo maker: one box, routes to the same-game-aware (mixed) builder
// when the checkbox is on, else the one-leg-per-game parlay builder.
window.buildCombo = async () => {
  const out = $("comboOut");
  if (!out) return;
  let n = parseInt(($("comboN") || {}).value, 10); if (isNaN(n) || n < 2) n = 2;
  let t = parseInt(($("comboTarget") || {}).value, 10); if (isNaN(t)) t = 65;
  let p = parseFloat(($("comboPayout") || {}).value) || 0;
  parlayLegs = n; parlayTarget = t; parlayPayout = p;
  const sameGame = $("comboSameGame") && $("comboSameGame").checked;
  const legsMode = ($("comboLegsMode") || {}).value || "prefer";
  const payoutMode = ($("comboPayoutMode") || {}).value || "off";
  const conn = ($("comboConn") || {}).value || "or";
  const date = $("bbDate").value;
  // Both modes run through the simulator now, so every leg shows model vs sim.
  // same_game on may stack correlated legs from one game; off = one leg per game.
  simLoader(out, sameGame ? "Simulating games (correlated same-game odds)…" : "Simulating every game…");
  try {
    const q = `legs=${n}&target=${t}&payout=${p}&same_game=${sameGame ? 1 : 0}`
      + `&legs_mode=${legsMode}&payout_mode=${payoutMode}&conn=${conn}`;
    const d = await (await fetch(`/api/baseball/mixed?date=${date}&${q}`)).json();
    if (d.error === "upgrade_required") { out.innerHTML = upgradeNote(d); return; }
    if (d.error) { out.innerHTML = `<div class="small">${d.error}</div>`; return; }
    if (!d.parlay) { out.innerHTML = `<div class="small">Couldn't build — need upcoming games.</div>`; return; }
    out.innerHTML = renderMixed(d.parlay);
  } catch (e) {
    out.innerHTML = `<div class="small">Build failed — try again.</div>`;
  }
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
function renderBreakdown(b, n) {
  if (!b) return "";
  const nf = (v) => (v == null ? 0 : v).toLocaleString();
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
  return `<span>Kalshi pays <b>${m.kalshi_payout_x}×</b>${partial}</span>`;
}

function renderSGP(s) {
  const nsim = s.n_sims || 0;
  const legs = s.legs.map((l) =>
    `<li><span class="legtag">${l.type}</span> ${l.pick} ${legProb(l, nsim)}</li>`).join("");
  const corr = s.corr_delta_pct;
  const corrTxt = corr > 0.4 ? `<b style="color:#3ad17a">legs reinforce (+${corr}% vs independent)</b>`
    : corr < -0.4 ? `<b style="color:#e0566a">legs fight each other (${corr}% vs independent)</b>`
    : `<span style="color:var(--muted)">~independent (${corr >= 0 ? "+" : ""}${corr}%)</span>`;
  const cnt = s.combined_sims_hit != null ? ` <span style="color:var(--muted)">(${s.combined_sims_hit.toLocaleString()}/${nsim.toLocaleString()} sims)</span>` : "";
  return `<div class="combo hl prop">
    <div class="chead">
      <span class="ctag">🎰 ${s.matchup}</span>
      <span class="small">${s.n_legs} legs · ${nsim.toLocaleString()} sims</span>
    </div>
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
      `<li><span class="legtag">${l.type}</span> ${l.pick} ${legProb(l)}</li>`).join("");
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
    loadPropLog();

    const c = d.combos;
    bbCombosData = c;
    let html = "";
    // Unified combo maker: one box, with a checkbox to allow same-game stacking.
    const maxN = c.max_legs_available || 0;
    if (maxN >= 2) {
      const def = Math.min(parlayLegs, maxN);
      const sel = (id, opts, cur) => `<select id="${id}" style="width:auto;padding:2px 4px">`
        + opts.map(([v, lbl]) => `<option value="${v}"${v === cur ? " selected" : ""}>${lbl}</option>`).join("") + `</select>`;
      html += `<div class="combomaker">
        🎯 <b>Combo maker</b> — each leg ≥
        <input id="comboTarget" type="number" min="20" max="97" value="${parlayTarget}" style="width:54px"/>% likely
        <div class="small" style="margin-top:6px">
          ${sel("comboLegsMode", [["prefer", "recommend"], ["require", "require"], ["off", "off"]], "prefer")}
          <input id="comboN" type="number" min="2" max="12" value="${def}" style="width:50px"/> legs
          &nbsp;${sel("comboConn", [["or", "OR"], ["and", "AND"]], "or")}&nbsp;
          ${sel("comboPayoutMode", [["off", "off"], ["prefer", "recommend"], ["require", "require"]], parlayPayout > 1 ? "require" : "off")}
          reach <input id="comboPayout" type="number" min="0" step="any" value="${parlayPayout}" style="width:60px"/>× payout
        </div>
        <label class="small" style="display:inline-block;margin-top:6px"><input type="checkbox" id="comboSameGame" style="width:auto"/> allow same-game parlays ${lockTag("mixed_parlay")}</label>
        <button class="track-mini primary-mini" onclick="buildCombo()">Build</button>
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
    html += `<div class="small" style="margin:14px 0 4px"><b>Game-winner parlays</b> — by combined chance:</div>`;
    html += c.all.map((x) => renderCombo(x)).join("");
    if (c.mixed && c.mixed.length) {
      html += `<div class="small" style="margin:14px 0 4px"><b>🎲 Mixed combos (incl. props)</b> — moneyline, run line, totals &amp; hit props, one leg per game:</div>`;
      html += c.mixed.map((x) => renderCombo(x)).join("");
    }
    // Preserve a built combo slip (and the same-game toggle) across the
    // auto-refresh so it isn't wiped while you're reading/screenshotting it.
    const prevCombo = (() => { const el = $("comboOut"); return el ? el.innerHTML : ""; })();
    const prevSameGame = !!($("comboSameGame") && $("comboSameGame").checked);
    combosBox.innerHTML = html;
    if (prevCombo) { const el = $("comboOut"); if (el) el.innerHTML = prevCombo; }
    if (prevSameGame) { const cb = $("comboSameGame"); if (cb) cb.checked = true; }
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
      $("tab-edges").classList.toggle("hidden", tab !== "edges");
      $("tab-hits").classList.toggle("hidden", tab !== "hits");
      $("tab-football").classList.toggle("hidden", tab !== "football");
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
      if (tab === "hits" && !$("hitsDate").dataset.loaded) {
        $("hitsDate").dataset.loaded = "1";
        initHits();
      }
      if (tab === "edges" && !$("edgeDate").dataset.loaded) {
        $("edgeDate").dataset.loaded = "1";
        initEdges();
      }
    });
  });
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

function hitChip(p) {
  const cls = p.hit ? "hitchip win" : "hitchip miss";
  const mk = p.market_pct != null ? ` · mkt ${p.market_pct}¢` : "";
  const pay = p.payout_x != null ? ` · ${p.payout_x}×` : "";
  const ed = (p.edge != null) ? ` <span class="${p.edge >= 0 ? "ev pos" : "ev neg"}">${p.edge >= 0 ? "+" : ""}${p.edge}</span>` : "";
  return `<div class="${cls}">
    <span class="hitmark">${p.hit ? "✅" : "❌"}</span>
    <span class="hitlabel">${p.label}</span>
    <span class="small">model <b>${p.model_pct != null ? p.model_pct + "%" : "—"}</b>${mk}${ed}${pay}</span>
  </div>`;
}

function renderHits(d) {
  if (!d.graded_n) {
    const rec = d.recorder || {};
    return `<div class="empty">Nothing graded yet for this slate.<br>
      <span class="small">The recorder logs props every ~10 min and grades them once games go final${rec.logged != null ? ` (so far: ${rec.logged} logged)` : ""}. Check back later tonight.</span></div>`;
  }
  const s = d.predicted_summary || {};
  const sumLine = s.recommended
    ? `<div class="small" style="margin:2px 0 8px">Of <b>${s.recommended}</b> props the model liked (≥55%), <b class="${(s.hit_pct||0) >= 50 ? "ev pos" : "ev neg"}">${s.hit}</b> hit (${s.hit_pct}%). Honest record — misses shown too.</div>`
    : "";
  const predicted = (d.predicted || []).length
    ? d.predicted.map(hitChip).join("")
    : `<div class="small">No model-liked props have graded for this slate yet.</div>`;
  const risky = (d.risky || []).length
    ? d.risky.map(hitChip).join("")
    : `<div class="small">No longshots cashed this slate (or none graded yet).</div>`;
  return `
    <div class="hitsec"><div class="hitsechead">🎯 Predicted hits — props the model liked</div>
      ${sumLine}${predicted}</div>
    <div class="hitsec"><div class="hitsechead">🍀 Risky hits — longshots that cashed</div>
      <div class="small" style="margin:2px 0 8px">If you'd bet these cheap YES prices, here's what they'd have paid. (Hindsight board — not advice.)</div>
      ${risky}</div>`;
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
  box.innerHTML = `<div class="empty">Scanning every priced leg across the slate… (simulating each game, a few seconds)</div>`;
  $("edgeSummary").innerHTML = "";
  try {
    const d = await (await fetch(`/api/baseball/edges?date=${date}&min_edge=4`)).json();
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
  rows.sort((a, b) => b.edge - a.edge);
  if (!rows.length) { box.innerHTML = `<div class="empty">Nothing at that filter. Lower the min edge or change the side.</div>`; return; }

  const head = `<div class="edgerow edgehead">
    <span class="ecol-edge">Edge</span><span class="ecol-pick">Leg</span>
    <span class="ecol-num">Our %</span><span class="ecol-num">Kalshi</span>
    <span class="ecol-num">Pays</span><span class="ecol-conf">Trust</span></div>`;
  const body = rows.map((r) => {
    const cls = r.edge >= 0 ? "pos" : "neg";
    const model = (r.model_pct != null) ? ` <span class="emodel">model ${r.model_pct}%</span>` : "";
    return `<div class="edgerow">
      <span class="ecol-edge ev ${cls}">${r.edge >= 0 ? "+" : ""}${r.edge}</span>
      <span class="ecol-pick"><b>${r.pick}</b><span class="emu">${r.matchup}</span></span>
      <span class="ecol-num">${r.our_pct}%${model}</span>
      <span class="ecol-num">${r.market_cents}¢</span>
      <span class="ecol-num">${r.market_payout_x}×</span>
      <span class="ecol-conf conf-${r.confidence}">${CONF_LABEL[r.confidence] || r.confidence}</span>
    </div>`;
  }).join("");
  box.innerHTML = head + body +
    `<div class="small" style="margin-top:10px;color:var(--muted)">Showing ${rows.length} of ${d.n_priced} priced legs. <b>Edge</b> = our simulated chance − Kalshi's price. <b>Trust</b> reflects how well-grounded the model is for that market (Ks/Total/ML strongest). Positive edge = we think YES is likelier than the market.</div>`;
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
    let banner = "";
    if (d.racing_locked) {
      banner = `<div class="note" style="border:1px solid var(--accent);color:var(--accent)">🔒 Grid-based win model & edge picks for racing need the ${tierLabel("pro")} tier. <button class="track-mini primary-mini" onclick="window.bumpTier('pro')">Unlock</button></div>`;
    } else if (d.grid && d.grid.available) {
      const basis = d.grid.form_used ? "grid + recent form" : "grid";
      banner = `<div class="small" style="margin:2px 0 8px">🏁 Model using <b>${d.grid.race}</b> ${basis} (${d.grid.series}, ${d.grid.field}-car field). Edge = model win% − Kalshi price.</div>`;
    } else if (d.grid && !d.grid.available) {
      banner = `<div class="small" style="margin:2px 0 8px">🏁 ${d.grid.reason} — showing market-favorite picks until qualifying posts.</div>`;
    }
    box.innerHTML = banner + d.events.map((e) => renderSportEvent(e, key)).join("");
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
  const dfsMlbToggle = () => { if ($("dfsMlbOpts")) $("dfsMlbOpts").classList.toggle("hidden", $("dfsSport").value !== "mlb"); };
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
    return `<div class="dfs-prow">
      <div class="dfs-pmain"><span class="legtag">${p.pos}</span> <b>${p.name}</b>
        ${p.team ? `<span class="dfs-team">${p.team}</span>` : ""} ${sharpBadge(p.sharp)}</div>
      <div class="dfs-pmeta">$${p.salary.toLocaleString()} · <span title="simulated">${star}</span> proj <b>${p.proj}</b> · ceil ${p.ceil} · own ${own} ${lev(p.lev)}</div>
    </div>`;
  };

  const lineupCard = (ln, i) => {
    const cs = d.contest_sim && d.contest_sim.lineups && d.contest_sim.lineups[i];
    const csLine = cs ? `<div class="dfs-csrow">
        <span>win <b>${cs.win_pct}%</b></span><span>cash <b>${cs.cash_pct}%</b></span>
        <span>ROI <b class="${cs.roi_pct >= 0 ? "ev pos" : "ev neg"}">${cs.roi_pct >= 0 ? "+" : ""}${cs.roi_pct}%</b></span></div>` : "";
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

  let csHead = "";
  if (d.contest_sim && !d.contest_sim.error) {
    const c = d.contest_sim;
    csHead = `<div class="dfs-note">🏆 Simulated vs a ${c.field_size}-lineup ${c.contest === "double_up" ? "double-up" : "GPP"} field over ${c.iterations} runs — win% / cash% / ROI per lineup below. <i>Field &amp; payout are modeled estimates.</i></div>`;
  } else if (d.contest_sim && d.contest_sim.error) {
    csHead = `<div class="dfs-note">${d.contest_sim.error}</div>`;
  }

  const un = d.unmatched && d.unmatched.length
    ? `<div class="small" style="color:var(--muted);margin-top:8px">${d.unmatched.length} CSV players had no sim/projection match (skipped): ${d.unmatched.slice(0, 6).join(", ")}${d.unmatched.length > 6 ? "…" : ""}</div>` : "";

  return `<div class="dfs-wrap">
    <div class="dfs-top">
      <div class="dfs-title">⚾ MLB DFS — ${d.objective === "ceiling" ? "GPP (ceiling)" : "Cash (median)"}</div>
      <div class="dfs-meta">${d.n_lineups} lineup${d.n_lineups > 1 ? "s" : ""} · ${d.sim_players} sim-projected players in pool of ${d.pool}</div>
    </div>
    ${boardHtml}
    ${csHead}
    <div class="dfs-lineups">${d.lineups.map(lineupCard).join("")}</div>
    ${expHtml}
    ${un}
    <div class="small" style="margin-top:8px;color:var(--muted)">🎲 hitters come from the correlated game sim (stacking rewards the ceiling); pitchers are simulated from rate stats. Ownership/leverage are model estimates. Needs posted lineups (a few hours pre-game).</div>
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
        field_size: parseInt(($("dfsField") || {}).value, 10) || 200 }),
    })).json();
    if (d.error === "upgrade_required") { box.innerHTML = upgradeNote(d); return; }
    if (d.error) { box.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    if (d.total_ceil != null) { box.innerHTML = renderMlbDfs(d); return; }   // sim-driven MLB
    const rows = d.lineup.map((p) => {
      const startTag = p.start != null ? `<span class="legtag">P${p.start}</span> ` : "";
      let pd = "";
      if (p.pd_adj != null && Math.abs(p.pd_adj) >= 0.1) {
        const cls = p.pd_adj > 0 ? "#3ad17a" : "#e0566a";
        pd = ` <span style="color:${cls}">(${p.pd_adj > 0 ? "+" : ""}${p.pd_adj} PD, was ${p.base_proj})</span>`;
      }
      return `<div class="sportout"><div class="left"><span class="oname">${startTag}${p.captain ? "⭐ " : ""}${p.name}${p.captain ? " (CPT 1.5×)" : ""}</span><span class="small">$${p.salary.toLocaleString()} · proj ${p.proj}${pd}</span></div></div>`;
    }).join("");
    let gridBanner = "";
    const g = d.grid;
    if (g && g.available) {
      const un = g.unmatched && g.unmatched.length
        ? ` · <span style="color:var(--muted)">${g.unmatched.length} unmatched (no grid adj)</span>` : "";
      const fb = g.form_used ? " + recent form" : "";
      gridBanner = `<div class="small" style="margin:4px 0 0">🏁 Grid: <b>${g.race}</b> (${g.series}, ${g.field}-car field) — ${g.matched} drivers matched${un}. Projections adjusted for place differential off the actual qualifying order${fb}.</div>`;
    } else if (g && !g.available) {
      gridBanner = `<div class="small" style="margin:4px 0 0">🏁 ${g.reason} — using season points only (no place-differential adjustment yet).</div>`;
    }
    box.innerHTML = `<div class="bbgame">
      <div class="matchup">Optimal ${d.roster}-player lineup (${d.pool} in pool)</div>
      ${gridBanner}
      <div class="kv" style="margin-top:6px"><span>Salary <b>$${d.total_salary.toLocaleString()}</b> / $${d.cap.toLocaleString()}</span>
        <span>Projected <b>${d.total_proj}</b> pts</span></div>
      <div class="kv"><span>🔴 Floor <b>${d.sim.floor}</b></span><span>Median <b>${d.sim.median}</b></span>
        <span>🟢 Ceiling <b class="ev pos">${d.sim.ceiling}</b></span><span>Max <b>${d.sim.max}</b></span></div>
      <div class="sportouts" style="margin-top:8px">${rows}</div>
      <div class="small" style="margin-top:6px">Floor/ceiling are the 10th/90th-percentile simulated totals — the ceiling is what matters for GPP tournaments.${g && g.available ? " <b>PD</b> = place-differential adjustment: a driver starting better than his car deserves loses expected points; one buried deep gains." : ""}</div>
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
      const c = d.combo;
      let title, note = "";
      if (p > 1) {
        title = `🎰 ${c.legs_used || c.n_legs}-leg mega parlay → ${c.fair_payout_x}× (every leg ≥ ${t}%)`;
        if (c.expanded) note += `<div class="small">Added legs up to <b>${c.legs_used}</b> (you asked ${c.requested_legs}) to reach ${p}× while keeping every leg ≥ ${t}%.</div>`;
        if (c.payout_reached === false) note += `<div class="small">⚠️ Couldn't reach ${p}× with every leg ≥ ${t}% — the max at that floor is <b>${c.fair_payout_x}×</b>. Lower the floor or target, or add categories.</div>`;
        note += `<div class="small">At ${c.fair_payout_x}× the chance is ~<b>${c.combined_prob_pct}%</b> (≈1 in ${Math.round(c.fair_payout_x)}). For fair-odds legs that's the same however you split them — extra legs just land closer to the target.</div>`;
      } else {
        title = `🎰 ${c.n_legs}-leg mega parlay (≥${t}%)`;
        if (c.legs_meeting_target != null) note = `<div class="small">${c.legs_meeting_target}/${c.n_legs} legs meet the ${t}% target.</div>`;
      }
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
  if ($("valBtn")) $("valBtn").addEventListener("click", loadValue);
  if ($("propLogBtn")) $("propLogBtn").addEventListener("click", loadPropLog);

  // Sports setup
  $("sportBtn").addEventListener("click", loadSports);
  $("sportSel").addEventListener("change", loadSports);

  // Weather setup
  $("wxBtn").addEventListener("click", loadWeather);
  $("wxCity").addEventListener("change", loadWeather);
  if ($("wxDate")) $("wxDate").addEventListener("change", loadWeather);

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
