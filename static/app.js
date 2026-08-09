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

// Kalshi's combo payout ceiling, used only to LABEL the max-bet buttons. The
// server owns the real value (combo_engine.MAX_PAYOUT_X, overridable by env), so
// every max-bet response carries cap_x and this is updated from it — the default
// here just has to be right often enough to render a sensible button before the
// first build.
let MAX_BET_X = 435;
function noteMaxBetCap(d) {
  const c = (d && (d.cap_x || (d.parlay && d.parlay.cap_x) || (d.combo && d.combo.cap_x)));
  if (c && c > 1) MAX_BET_X = c;
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
    if (!c) return "";
    if (c.n >= 40 && Math.abs(c.t - 1) >= 0.03) {
      const verb = c.t > 1 ? "reining in overconfidence" : "sharpening (was underconfident)";
      return `<div class="small calnote" style="color:var(--muted);margin:2px 0 8px">🎯 Calibrated ${label}: <b>${(+c.t).toFixed(2)}×</b> — ${verb}, fit on ${c.n} settled markets.</div>`;
    }
    if ((c.logged || 0) > 0)
      return `<div class="small calnote" style="color:var(--muted);margin:2px 0 8px">🎯 Calibration for ${label}: <b>accruing</b> — ${c.logged} prediction${c.logged > 1 ? "s" : ""} logged, ${c.n} graded so far. Auto-activates once enough markets settle.</div>`;
    return "";
  } catch (e) { return ""; }
}
// Append the calibration note to a summary box, replacing any prior one. The
// note fetch is async and its render function reruns on every poll/refresh, so
// without this de-dup the banners stack up (one per refresh).
function appendCalNote(elId, model, label) {
  calNote(model, label).then((h) => {
    const el = $(elId);
    if (!el) return;
    el.querySelectorAll(".calnote").forEach((n) => n.remove());
    if (h) el.insertAdjacentHTML("beforeend", h);
  });
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
        <div class="teamhdr">Run line — Kalshi "wins by over X" (adjustable)</div>
        ${[[rl.home, rl.home_by], [rl.away, rl.away_by]].map(([tm, by]) => by
          ? `<div class="small"><b>${tm} by over</b> ` + Object.entries(by).map(([m, p]) => `${(+m - 0.5)} <b>${p}%</b>`).join(" · ") + `</div>`
          : `<div class="small"><b>${tm} by over 1.5</b>: <b>${tm === rl.home ? rl.home_by2_pct : rl.away_by2_pct}%</b></div>`).join("")}
        <div class="small" style="color:var(--muted)">Pre-game Kalshi books 1.5 / 2.5 / 3.5; once a game is live and the runs are in it adds 4.5 or drops back to 2.5, and this follows whatever it lists.</div>
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
  // De-vig: the ask you pay includes the overround (both teams' asks sum to >100).
  // edge-vs-fair strips it out, so you can tell a genuine model disagreement from
  // just the vig — a small negative vs the ASK but ~0 vs FAIR means the model
  // agrees with Kalshi and you're only seeing the house margin.
  if (g.fair_prob != null && g.edge_vs_fair != null) {
    const ef = g.edge_vs_fair;
    market += ` <span class="small" style="color:var(--muted)" title="Kalshi's two team asks sum to ${(100 + (g.vig_cents || 0)).toFixed(0)}¢ — the ${g.vig_cents}¢ over 100 is the vig. Fair strips it out.">· vs fair ${g.fair_prob}¢ <b class="${ef >= 0 ? "ev pos" : "ev neg"}">${ef >= 0 ? "+" : ""}${ef}</b> <span style="opacity:.7">(vig ${g.vig_cents}¢)</span></span>`;
  }
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
let parlayCap = 0;          // 0 = no ceiling; >0 turns the floor into a band
let parlayPayout = 0;
// Combo-maker controls persist across the 20s auto-refresh (the refresh re-renders
// the maker, so without this the selects snap back to defaults — which used to
// revert AND→OR and detach the in-flight result. See comboBuilding guard below.)
let comboLegsModePref = "prefer";
let comboPayoutModePref = "off";
let comboConnPref = "or";
// Which point on the price/probability frontier the maker returns.
let comboObjectivePref = "balanced";
let comboSameGamePref = false;
let comboIncludeLive = false;
let comboGameSel = null;   // null/empty = ALL games; else {pk: true|teamName} selection
let comboBuilding = false; // true while a build is in flight -> pauses auto-refresh

// Prop-type filter for the combo/edge builders. Empty set = all types allowed.
let _mlbTypes = new Set();
const _MLB_TYPES = [["ML", "Moneyline"], ["Total", "Totals"], ["Run line", "Run line"],
  ["Hit", "Hits"], ["HR", "Home runs"], ["Bases", "Total bases"], ["Ks", "Strikeouts"],
  ["RFI", "1st-inn run"], ["HRR", "H+R+RBI"], ["SB", "Stolen bases"]];
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

// How many games on the loaded slate are actually under way right now.
function liveGameCount() {
  const gs = (bbCombosData && bbCombosData.games) || [];
  return gs.filter((g) => g.live).length;
}

// Shown whenever live pricing is switched on. Two separate cautions: the board
// is a snapshot that can be stale within a pitch, and a live leg inside a
// multi-game slip drags the whole slip's timing with it.
function liveWarnHtml() {
  if (!comboIncludeLive) return "";
  const n = liveGameCount();
  const multi = (bbCombosData && bbCombosData.games || []).length > 1;
  return `<div class="livewarn">
    <b>⚠️ Live pricing is on</b> — ${n ? `${n} game${n === 1 ? " is" : "s are"}` : "games"} in progress will be
    simulated forward from the current score, count and base-out state, with whatever each
    player has already banked counted toward his line. Prices come from the live Kalshi market.
    <div style="margin-top:4px">This board is a <b>snapshot</b>: one pitch can move it. Re-build right before you place.</div>
    ${multi ? `<div style="margin-top:4px">In a <b>multi-game parlay</b>, a live leg can settle long before the rest — the slip is only decided when every game finishes.</div>` : ""}
  </div>`;
}

window.renderLiveWarn = () => {
  const el = $("liveWarn");
  if (el) el.innerHTML = liveWarnHtml();
};

// Unified combo maker: one box, routes to the same-game-aware (mixed) builder
// when the checkbox is on, else the one-leg-per-game parlay builder.
window.buildCombo = async (maxBet) => {
  const out = $("comboOut");
  if (!out) return;
  let n = parseInt(($("comboN") || {}).value, 10); if (isNaN(n) || n < 2) n = 2;
  let t = parseInt(($("comboTarget") || {}).value, 10); if (isNaN(t)) t = 65;
  let c = parseInt(($("comboCap") || {}).value, 10); if (isNaN(c) || c <= 0) c = 0;
  // A ceiling below the floor is meaningless — treat it as "no ceiling" rather
  // than silently building nothing.
  if (c && c < t) c = 0;
  let p = parseFloat(($("comboPayout") || {}).value) || 0;
  parlayLegs = n; parlayTarget = t; parlayPayout = p; parlayCap = c;
  // Persist the control choices so the auto-refresh re-renders them as-set.
  comboSameGamePref = !!($("comboSameGame") && $("comboSameGame").checked);
  comboLegsModePref = ($("comboLegsMode") || {}).value || "prefer";
  comboPayoutModePref = ($("comboPayoutMode") || {}).value || "off";
  comboConnPref = ($("comboConn") || {}).value || "or";
  comboObjectivePref = ($("comboObjective") || {}).value || "balanced";
  const date = $("bbDate").value;
  // Both modes run through the simulator now, so every leg shows model vs sim.
  // same_game on may stack correlated legs from one game; off = one leg per game.
  comboBuilding = true;
  simLoader(out, maxBet ? `Searching for the likeliest slip that pays ${MAX_BET_X}×…`
    : comboSameGamePref ? "Simulating games (correlated same-game odds)…" : "Simulating every game…");
  try {
    let q = `legs=${n}&target=${t}&payout=${p}&same_game=${comboSameGamePref ? 1 : 0}`
      + `&legs_mode=${comboLegsModePref}&payout_mode=${comboPayoutModePref}&conn=${comboConnPref}`
      + `&include_live=${comboIncludeLive ? 1 : 0}&objective=${comboObjectivePref}`
      + (c ? `&cap=${c}` : "")
      + (maxBet ? "&max_bet=1" : "");
    const selParam = comboSelParam();
    if (selParam) q += `&sel=${encodeURIComponent(selParam)}`;
    q += mlbTypesParam();
    const d = await (await fetch(`/api/baseball/mixed?date=${date}&${q}`)).json();
    noteMaxBetCap(d);
    if (d.error === "upgrade_required") { out.innerHTML = upgradeNote(d); return; }
    if (d.error) { out.innerHTML = `<div class="small">${d.error}</div>`; return; }
    if (!d.parlay) {
      out.innerHTML = (d.hint === "max_bet_unreachable")
        ? `<div class="small">No slip on today's slate can pay <b>${d.cap_x || MAX_BET_X}×</b>. Every leg needs a real Kalshi quote behind it, so a short or thin slate runs out before the ceiling. Try again with more games selected, or once more of the board opens.</div>`
        : `<div class="small">Couldn't build — no eligible games for that selection.${c ? ` No market on the slate lands between <b>${t}%</b> and <b>${c}%</b>; try widening the band.` : ""} Try ALL GAMES, allow live, or loosen a target.</div>`;
      return;
    }
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
  // Chronological, earliest on the left, ALWAYS -- independent of the slate's
  // sort control. The grid scrolls sideways, so "the first few games" has to be
  // the first few cards or it means walking the whole strip to find them.
  // Games already under way sort first, which is where they belong in time.
  // A game with no usable time sorts last rather than to 1970 -- and note
  // Date.parse(0) is year 2000, not the epoch, so the fallback parses `start`
  // only when there is a string to parse.
  const at = (g) => {
    if (g.start_epoch) return g.start_epoch;
    const t = g.start ? Date.parse(g.start) : NaN;
    return isNaN(t) ? Infinity : t / 1000;
  };
  const elig = games.filter((g) => ((g.live || {}).state) !== "Final")
    .slice()
    .sort((a, b) => at(a) - at(b));
  const allOn = !comboGameSel || !Object.keys(comboGameSel).length;
  const esc = (s) => (s || "").replace(/'/g, "\\'");
  let cards = `<div class="gg-card gg-all${allOn ? " on" : ""}" onclick="comboSelectAll()">ALL<br>GAMES</div>`;
  cards += elig.map((g) => {
    const pk = g.game_pk, sel = comboGameSel ? comboGameSel[pk] : undefined;
    const live = (g.live || {}).state === "Live";
    const aOn = sel === true || sel === g.away_name;
    const hOn = sel === true || sel === g.home_name;
    const when = fmtStartTime(g.start);
    return `<div class="gg-card${sel === true ? " on" : ""}" onclick="comboToggleGame(${pk})" title="${g.matchup || ""}${when ? ` — ${when}` : ""}">
      ${live ? '<span class="gg-live">🔴 LIVE</span>' : ""}
      <span class="gg-team${aOn ? " on" : ""}" onclick="event.stopPropagation();comboToggleTeam(${pk},'${esc(g.away_name)}')">${teamShort(g.away_name)}</span>
      <span class="gg-vs">vs</span>
      <span class="gg-team${hOn ? " on" : ""}" onclick="event.stopPropagation();comboToggleTeam(${pk},'${esc(g.home_name)}')">${teamShort(g.home_name)}</span>
      ${(!live && when) ? `<span class="gg-when">${when.replace(" MT", "")}</span>` : ""}
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
    const liveQ = comboIncludeLive ? "&live=1" : "";
    const d = await (await fetch(`/api/baseball/parlay?date=${date}&legs=${n}&target=${t}&payout=${p}${liveQ}`)).json();
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
    // A quote nobody can fill is not a price. It still shows — seeing the
    // market's number is useful — but greyed, without an edge, because an edge
    // against a book with no depth behind it is not something you can take.
    mkt = (l.fillable === false)
      ? ` <span class="kmkt" style="opacity:.55">Kalshi <b>${l.market_cents}¢</b> <span style="color:var(--muted)">thin</span></span>`
      : ` <span class="kmkt">Kalshi <b>${l.market_cents}¢</b>${l.market_payout_x ? ` (${l.market_payout_x}×)` : ""}` +
        ` <span class="${edge >= 0 ? "ev pos" : "ev neg"}">${edge >= 0 ? "+" : ""}${edge}</span></span>`;
  } else if (l.market_cents === null && l.kref) {
    mkt = ` <span class="kmkt" style="opacity:.55">no Kalshi market</span>`;
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
    `<li><span class="legtag">${l.type}</span> ${liveTag(l)}${sideTag(l)}${l.pick} ${legProb(l, nsim)}${simAvgTag(l)}</li>`).join("");
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
      `<li><span class="legtag">${l.type}</span> ${liveTag(l)}${sideTag(l)}${l.pick} ${legProb(l)}${simAvgTag(l)}</li>`).join("");
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
  // With a ceiling set, say so on the slip — every leg above is inside the band
  // by construction, and the lines shown are the ones that got it there.
  const bandNote = (m.leg_cap_pct != null)
    ? `<span>every leg <b>${m.leg_floor_pct}–${m.leg_cap_pct}%</b></span>` : "";
  // Every leg on a slip is bettable on Kalshi by construction now; say how much
  // of the pool that rule removed, so a thin morning build (lines post near
  // game time) is explainable instead of mysterious.
  const unpricedNote = m.excluded_unpriced
    ? `<span style="color:var(--muted)" title="Legs the model liked but Kalshi doesn't list (or hasn't posted yet) are excluded — a slip you can't place isn't a slip. Pools widen as lines post near game time.">${m.excluded_unpriced.toLocaleString()} unlisted legs excluded</span>` : "";
  // Name what was honoured and what was impossible. "Couldn't satisfy your
  // target(s)" was true but useless: with a required leg count AND a required
  // payout it never said which one broke, and the builder used to quietly drop
  // BOTH. A required leg count is now always honoured, so the message is about
  // the one target that genuinely could not be reached at that size.
  const unmet = m.unmet || [];
  let hardWarn = "";
  if (m.hard_ok === false && unmet.length) {
    const held = [];
    if (m.legs_target != null && !unmet.includes("legs")) held.push(`<b>${m.legs_target} legs</b>`);
    if (m.target_payout_x && !unmet.includes("payout")) held.push(`<b>≥${m.target_payout_x}×</b>`);
    const heldTxt = held.length ? ` Held your ${held.join(" and ")}.` : "";
    if (unmet.includes("payout")) {
      const best = m.best_payout_at_legs || m.fair_payout_x;
      hardWarn = `<div class="small" style="margin-top:4px;color:#e0566a">⚠️ <b>${m.target_payout_x}× isn't reachable with ${m.n_legs} legs</b> at your per-leg floor — the most any ${m.n_legs}-leg parlay pays today is <b>${best}×</b>.${heldTxt} To reach ${m.target_payout_x}×: add legs, or lower the per-leg % so longer shots qualify.</div>`;
    } else {
      hardWarn = `<div class="small" style="margin-top:4px;color:#e0566a">⚠️ Couldn't satisfy <b>${unmet.join(" or ")}</b> on today's slate — showing the closest parlay.${heldTxt}</div>`;
    }
  }
  const stacked = m.groups.some((g) => g.same_game);
  // Price-side numbers. EV is what the slip returns per $1 at Kalshi's actual
  // asks including the taker fee, so a negative one means the likeliest slip is
  // still a long-run loser — worth saying out loud rather than burying.
  const evTxt = (m.ev_pct == null) ? "" :
    `<span>EV at market <b style="color:${m.ev_pct >= 0 ? "#3ad17a" : "#e0566a"}">${m.ev_pct >= 0 ? "+" : ""}${m.ev_pct}%</b></span>`;
  // Apply the user's Kelly fraction, exactly like every other bet on the site.
  // The backend returns FULL Kelly, which for a parlay is a dangerous number to
  // put in front of someone: a live slip came back at 44% of bankroll. Full Kelly
  // is only optimal when the probability is KNOWN, and a parlay's probability is
  // a product of estimates — each leg's error multiplies, so the true uncertainty
  // is much wider than a single bet's and full Kelly badly overbets it. The rest
  // of the app already defaults to ½; this was the one place that didn't.
  const _km = getKellyMult();
  const _kf = (m.kelly_pct || 0) * _km;
  const _klabel = _km === 1 ? "full" : _km === 0.25 ? "¼" : _km === 0.5 ? "½" : `${_km}×`;
  const kellyTxt = (_kf > 0)
    ? `<span>Stake <b>${_kf.toFixed(2)}%</b> of roll (${_klabel}-Kelly)</span>` : "";
  const evWarn = (m.ev_ok === false)
    ? `<div class="small" style="margin-top:4px;color:#e0566a">⚠️ No slip on today's slate clears break-even at Kalshi's prices with enough legs actually quoted — this is the closest. On an exchange that is normal: you pay the spread and a fee on every leg. Consider fewer legs, or skip today.</div>` : "";
  const unpriced = (m.priced_frac != null && m.priced_frac < 1)
    ? `<div class="small" style="margin-top:4px;color:var(--muted)">${Math.round(m.priced_frac * 100)}% of these legs have a live Kalshi quote; the rest are priced at fair value, so the EV above only reflects the ones you can actually place.</div>` : "";
  const alts = renderAlternatives(m);
  const maxNote = maxBetNote(m);
  return `<div class="combo hl prop">
    <div class="chead">
      <span class="ctag">${m.objective === "max_bet" ? "🎰 Max bet"
        : stacked ? "🔀 Mixed parlay" : "🎯 Cross-game parlay"}</span>
      <span class="small">${m.n_legs} legs · ${m.n_games} games · ${(m.n_sims || 0).toLocaleString()} sims</span>
    </div>
    ${groups}
    <div class="cnums">
      <span>Combined chance <b>${m.combined_prob_pct}%</b></span>
      <span>Fair payout <b>${m.fair_payout_x}×</b></span>
      ${kalshiPayout(m)}
      ${evTxt}
      ${kellyTxt}
      ${legNote}
      ${payNote}
      ${bandNote}
      <span>Correlation: ${corrTxt}</span>
      ${unpricedNote}
    </div>
    ${maxNote}
    ${hardWarn}
    ${evWarn}
    ${unpriced}
    ${alts}
    <div class="small" style="margin-top:4px">Naive independent guess: <b>${m.indep_prob_pct}%</b> (${m.indep_payout_x}×). Same-game legs use simulated joint odds; different games multiply. <i>Fair payout is no-vig (1÷our probability) — Kalshi's actual combo pays a bit less (their margin); a much bigger gap means we disagree with the market on a leg.</i></div>
  </div>`;
}

// The max-bet block. Shown INSTEAD of the ordinary target notes, because a max
// bet has no leg or payout target — it has a ceiling, and the only questions are
// whether it was reached and how likely the slip that reached it is.
//
// Both probabilities are shown side by side on purpose. On a capped ticket our
// model and the market can disagree by a lot, and showing only our own number
// would advertise a chance the price does not agree with.
function maxBetNote(m) {
  if (m.objective !== "max_bet") return "";
  const cap = m.cap_x || MAX_BET_X;
  if (!m.cap_reached) {
    return `<div class="small" style="margin-top:4px;color:#e0566a">⚠️ <b>${cap}× isn't reachable on this board today.</b>`
      + ` The most any fully-quoted slip pays is <b>${m.best_payout_x}×</b> — that's what's shown above.`
      + ` Every leg has to have a real Kalshi quote behind it, so a thin board caps out early.</div>`;
  }
  const mk = (m.market_prob_pct == null) ? "" :
    `<span>Market says <b>${m.market_prob_pct}%</b></span>`;
  const opt = (m.optimism_x == null) ? "" :
    `<span>We're <b>${m.optimism_x}×</b> the market</span>`;
  const over = m.overshoot_x
    ? `<div class="small" style="margin-top:4px;color:var(--muted)">This slip prices at <b>${m.uncapped_payout_x}×</b>, but Kalshi pays at most <b>${cap}×</b> — the extra ${m.overshoot_x}× is thrown away. It was still chosen because it's the likeliest slip that clears the ceiling.</div>`
    : "";
  const warn = (m.optimism_ok === false)
    ? `<div class="small" style="margin-top:4px;color:#e0566a">⚠️ Every slip that reaches ${cap}× claims a bigger edge over the market than we're willing to believe. Treat the chance above as our model's opinion, not a forecast.</div>`
    : "";
  const floors = (m.max_bet_floors_tried || []).length > 1
    ? `<div class="small" style="margin-top:4px;color:var(--muted)">Tried per-leg floors ${m.max_bet_floors_tried.map((f) => f.floor_pct + "%").join(", ")} and kept the best.</div>`
    : "";
  return `<div class="cnums" style="margin-top:4px">
      <span>🎰 <b>Max bet</b> — pays the ${cap}× ceiling</span>
      ${mk}${opt}
    </div>${over}${warn}${floors}`;
}

// The same frontier ranked the other two ways. Shown so "the price mattered" is
// something you can see rather than something we assert — and when all three
// land on the same slip, that itself is the useful answer.
function renderAlternatives(m) {
  const a = m.alternatives;
  if (!a) return "";
  const label = { safe: "🛡️ Likeliest", value: "💰 Best value", balanced: "⚖️ Balanced" };
  const rows = Object.keys(a).filter((k) => a[k]).map((k) => {
    const v = a[k];
    const mine = v.same_as_chosen;
    return `<span style="${mine ? "font-weight:700" : "opacity:.75"}">${label[k] || k}: ${v.legs}L · ${v.prob_pct}%`
      + (v.ev_pct == null ? "" : ` · EV ${v.ev_pct >= 0 ? "+" : ""}${v.ev_pct}%`)
      + (mine ? " ←" : "") + `</span>`;
  });
  if (!rows.length) return "";
  return `<div class="cnums" style="margin-top:4px;font-size:.85em">${rows.join("")}</div>`;
}

// Small "avg sim 9.1 runs" tag shown under a combo leg when we have a simulated
// average for it (totals, Ks, hits, bases, HR, margins, goals, games, aces…).
function simAvgTag(l) {
  if (!l || l.sim_avg == null || !l.avg_unit) return "";
  const v = (l.avg_unit === "run margin" && l.sim_avg > 0) ? `+${l.sim_avg}` : l.sim_avg;
  return `<span class="legavg">avg sim ${v} ${l.avg_unit}</span>`;
}

// YES/NO badge for a combo leg. Kalshi settles almost every market as a yes/no
// contract, so a slip should say which side it's actually buying — an "Under"
// or a "NO —" leg is the NO contract, everything else is YES. RFI is the one
// exception the book doesn't split into sides, so it gets no badge.
// Marks a leg priced off a game already under way.
function liveTag(l) {
  return l.live ? `<span class="livetag" title="priced from the current game state">LIVE</span>` : "";
}

function sideTag(l) {
  const t = (l.type || "");
  if (t === "RFI" || /1st-inn/i.test(t)) return "";
  const lab = (l.pick || "");
  // Legs built from the sim carry their own side; fall back to reading the
  // label for the live-recomputed legs, which don't.
  const isNo = l.side ? l.side === "no"
    : (/\(NO\)\s*$/.test(t) || /^NO\s*[—-]/i.test(lab) || /\bunder\b/i.test(lab));
  return `<span class="sidetag ${isNo ? "no" : "yes"}">${isNo ? "NO" : "YES"}</span> `;
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
    return `<li>${liveDot}${typeTag}${sideTag(l)}${l.pick}${game}${where} <span style="color:var(--muted)">(${l.prob_pct}%${l.price_cents != null ? `, ${l.price_cents}¢` : ""})</span>${avg}</li>`;
  }).join("");
  let nums = `<span>Combined chance <b>${c.combined_prob_pct}%</b></span>
              <span>Fair payout <b>${c.fair_payout_x}×</b></span>`;
  if (c.ev_pct != null) {
    const netEv = c.ev_net_pct != null
      ? ` <span class="small" style="color:var(--muted)" title="after Kalshi's per-leg taker fees">(net ${c.ev_net_pct >= 0 ? "+" : ""}${c.ev_net_pct}%)</span>` : "";
    nums += `<span>Parlay payout <b>${c.parlay_payout_x}×</b></span>
             <span>EV <b class="${c.ev_pct >= 0 ? "ev pos" : "ev neg"}">${c.ev_pct >= 0 ? "+" : ""}${c.ev_pct}%</b>${netEv}</span>`;
  }
  // Whether the targets you REQUIRED were actually met. This was computed all
  // along and never shown: ask for "require 5x payout" on a board whose legs are
  // capped at 70% and the builder returns its best effort -- 3.94x -- with
  // nothing on screen to say the hard target was missed, so the slip reads like
  // the answer you asked for.
  const band = (c.target_pct != null)
    ? `<span>each leg <b>${c.target_pct}%${c.cap_pct != null ? `–${c.cap_pct}%` : "+"}</b></span>` : "";
  const legsNote = (c.legs_target != null && c.legs_mode && c.legs_mode !== "off")
    ? `<span>${c.legs_target} legs <b style="color:${c.legs_met === false ? "#e0566a" : "#3ad17a"}">${c.legs_met === false ? `✗ got ${c.legs_used}` : "✓"}</b></span>` : "";
  const payNote = (c.target_payout_x && c.payout_mode && c.payout_mode !== "off")
    ? `<span>payout ≥${c.target_payout_x}× <b style="color:${c.payout_reached ? "#3ad17a" : "#e0566a"}">${c.payout_reached ? "✓" : `✗ best ${c.fair_payout_x}×`}</b></span>` : "";
  let warn = "";
  if (c.hard_ok === false) {
    const miss = [];
    if (c.legs_met === false) miss.push("the leg count");
    if (c.target_payout_x && !c.payout_reached) miss.push(`a ${c.target_payout_x}× payout`);
    warn = `<div class="small" style="margin-top:4px;color:#e0566a">⚠️ Couldn't hit ${miss.join(" or ") || "one of your required targets"} on this board — showing the closest slip. ${
      (c.target_payout_x && !c.payout_reached && c.cap_pct != null)
        ? `A ceiling of ${c.cap_pct}% caps each leg's payout, so ${c.legs_used} of them can reach about ${c.fair_payout_x}× at most: raise the ceiling or add legs.`
        : "Loosen a target or add legs."}</div>`;
  }
  return `<div class="combo ${extraCls || ""}">
    <div class="chead">
      <span class="ctag">${tag || c.n_legs + "-team parlay"}</span>
      <span class="small">${c.n_legs} legs</span>
    </div>
    <ul class="legs">${legs}</ul>
    <div class="cnums">${nums}${band}${legsNote}${payNote}</div>
    ${maxBetNote(c)}
    ${warn}
  </div>`;
}

let _slatePoll = null;   // poll handle while the MLB slate builds
async function loadBaseball(silent) {
  const gamesBox = $("bbGames");
  const combosBox = $("bbCombos");
  const date = $("bbDate").value;
  if (!silent) {
    gamesBox.innerHTML = `<div class="empty">Loading slate…</div>`;
    combosBox.innerHTML = `<div class="empty">Crunching combos…</div>`;
  }
  try {
    const r = await fetch("/api/baseball/today?date=" + date);
    // 202 = the slate is still simulating. A cold build runs every game through
    // the engine, which is far longer than a request should be held open, so the
    // server answers immediately and we poll. Without this the request outlived
    // the worker timeout and came back as a 502 -- "Failed to load slate", with
    // nothing in the logs to say why.
    if (r.status === 202) {
      if (!silent) {
        gamesBox.innerHTML = `<div class="empty">Simulating every game on the slate… (~1 min cold, then it's cached)</div>`;
        combosBox.innerHTML = "";
      }
      clearTimeout(_slatePoll);
      _slatePoll = setTimeout(() => loadBaseball(true), 6000);
      return;
    }
    const d = await r.json();
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
    bbCombosData = Object.assign({}, c, { games: d.games || [] });
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
        <input id="comboTarget" type="number" min="20" max="97" value="${parlayTarget}" style="width:54px"/>%
        and ≤ <input id="comboCap" type="number" min="0" max="99" value="${parlayCap || ""}" placeholder="—" style="width:54px"/>% likely</div>
        <div class="small" style="margin-top:2px;color:var(--muted)">Leave the ceiling blank for no upper limit. Set one and each market walks to the line that lands in the band — Over 3.5 at 90% becomes Over 4.5 or 5.5, and a run line at 40% becomes the NO side.</div>
        <div class="small" style="margin-top:6px">goal
          ${sel("comboObjective", [["balanced", "⚖️ best odds that aren't -EV"], ["safe", "🛡️ likeliest, any price"], ["value", "💰 best value"]], comboObjectivePref)}
        </div>
        <div class="small" style="margin-top:6px">
          ${sel("comboLegsMode", [["prefer", "recommend"], ["require", "require"], ["off", "off"]], comboLegsModePref)}
          <input id="comboN" type="number" min="2" max="12" value="${def}" style="width:50px"/> legs
          &nbsp;${sel("comboConn", [["or", "OR"], ["and", "AND"]], comboConnPref)}&nbsp;
          ${sel("comboPayoutMode", [["off", "off"], ["prefer", "recommend"], ["require", "require"]], comboPayoutModePref)}
          reach <input id="comboPayout" type="number" min="0" step="any" value="${parlayPayout}" style="width:60px"/>× payout
        </div>
        <label class="small" style="display:inline-block;margin-top:6px"><input type="checkbox" id="comboSameGame"${(comboSameGamePref || sgOnly) ? " checked" : ""}${sgOnly ? " disabled" : ""} style="width:auto"/> allow same-game parlays ${lockTag("mixed_parlay")}</label>${sgOnly ? `<div class="small" style="margin-top:4px;color:var(--muted)">Only one game on the slate today — combos stack correlated legs from that game, priced with the correlation-aware sim.</div>` : ""}
        &nbsp;<label class="small" style="display:inline-block"><input type="checkbox" id="comboLive"${comboIncludeLive ? " checked" : ""} style="width:auto" onchange="comboIncludeLive=this.checked;renderLiveWarn()"/> 🔴 include games in progress</label>
        <div id="liveWarn">${liveWarnHtml()}</div>
        ${mlbTypeChipRow()}
        <div style="margin-top:6px">
          <button class="track-mini primary-mini" onclick="buildCombo()">Build</button>
          <button class="track-mini" style="margin-left:6px" onclick="buildCombo(true)" title="Ignore the settings above and build the likeliest slip that still pays Kalshi's ${MAX_BET_X}× ceiling">🎰 Max bet (${MAX_BET_X}×)</button>
        </div>
        <div class="small" style="margin-top:4px">Each target (legs / payout) can be a hard <b>require</b>, a soft <b>recommend</b>, or <b>off</b>; combine them with <b>AND</b>/<b>OR</b>. Every line (hits, bases, runs total, ML, run line, RFI, Ks) is simulated. <b>Same-game on</b> may stack correlated legs from one game; off keeps one leg per game.</div>
        ${modelLegend()}
        <div id="comboOut"></div>
      </div>`;
    }
    // The auto-built suggestion slips (safest / best value / best prop / live /
    // mixed) used to render here. They were rebuilt on every slate load whether
    // or not anyone scrolled to them -- ~26 MB and seconds of work per request on
    // an instance with none to spare. The combo MAKER above is unchanged and
    // builds the same slips on demand from whatever you select.
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

// Kalshi titles are exchange-authored text dropped straight into markup here,
// so they get escaped rather than trusted.
function escapeHtml(x) {
  return String(x == null ? "" : x).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------------------------------------------------------------------------
// Futures: long-dated Kalshi contracts, ranked by annual yield.
//
// Deliberately market-only — no model, no edge claim. The job here is to make
// the risk/return trade visible: every row shows what it pays per year NEXT TO
// the chance it pays nothing, because those are the same number seen twice.
// ---------------------------------------------------------------------------
let futLoaded = false;
let futTimer = null;

// --- Modeled futures: the markets our season sims actually price ------------
// The all-futures board can never show a positive expected return (at the
// market's own probability the fee makes every row negative). These rows can,
// because the model is allowed to disagree — so this is the default view.
let mfTimer = null;

function initFutures() {
  if (futLoaded) return;
  futLoaded = true;
  ["futSort", "futMinProb", "futMaxDays", "futMinVol"].forEach((id) =>
    $(id)?.addEventListener("change", loadFutures));
  $("futQ")?.addEventListener("input", () => {
    clearTimeout(futTimer);
    futTimer = setTimeout(loadFutures, 300);
  });
  ["mfSort", "mfMarket", "mfMaxDays", "mfPos", "mfSide", "mfInSeason"].forEach((id) =>
    $(id)?.addEventListener("change", loadModeledFutures));
  $("mfQ")?.addEventListener("input", () => {
    clearTimeout(mfTimer);
    mfTimer = setTimeout(loadModeledFutures, 300);
  });
  document.querySelectorAll("#futSubtabs .subtab").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll("#futSubtabs .subtab").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      const sub = b.dataset.futsub;
      $("futModeledView").classList.toggle("hidden", sub !== "modeled");
      $("futAllView").classList.toggle("hidden", sub !== "all");
      if (sub === "all") loadFutures(); else loadModeledFutures();
    });
  });
  loadModeledFutures();
}

function evCls(v) {
  return v >= 15 ? "good" : v > 0 ? "ok" : "meh";
}

async function loadModeledFutures() {
  const out = $("mfOut");
  if (!out) return;
  const q = ($("mfQ") || {}).value || "";
  const sort = ($("mfSort") || {}).value || "best";
  const mkt = ($("mfMarket") || {}).value || "";
  const maxDays = ($("mfMaxDays") || {}).value || "";
  const pos = ($("mfPos") || {}).value ?? "1";
  const side = ($("mfSide") || {}).value || "";
  const inSeason = $("mfInSeason") && $("mfInSeason").checked;
  out.innerHTML = `<div class="empty">Reading the season simulations…</div>`;
  try {
    let u = `/api/futures/modeled?limit=80&sort=${sort}&positive_only=${pos}`
      + `&q=${encodeURIComponent(q)}`;
    if (mkt) u += `&markets=${mkt}`;
    if (maxDays) u += `&max_days=${maxDays}`;
    if (inSeason) u += `&in_season=1`;
    const d = await (await fetch(u)).json();
    if (d.building) {
      out.innerHTML = `<div class="empty">Running the season simulations… first rows appear in a few seconds.</div>`;
      clearTimeout(mfTimer);
      mfTimer = setTimeout(loadModeledFutures, 3000);
      return;
    }
    if (d.error) { out.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    // The board publishes each model as it lands, so keep polling quietly while
    // the slower leagues finish rather than blocking on the whole set.
    if (d.partial) {
      clearTimeout(mfTimer);
      mfTimer = setTimeout(loadModeledFutures, 6000);
    }
    const cnt = $("futCount");
    if (cnt) cnt.textContent = `${d.total.toLocaleString()} shown · ${d.universe.toLocaleString()} modeled`
      + (d.partial ? " · still loading…" : "");
    if (side) d.rows = d.rows.filter((r) => (r.side || "yes") === side);
    if (!d.rows.length && d.partial) {
      out.innerHTML = `<div class="empty">Loaded ${(d.loaded || []).join(", ") || "…"} — still simulating the rest.</div>`;
      return;
    }
    if (!d.rows.length) {
      out.innerHTML = `<div class="empty">Nothing matches — try "everything" instead of only +EV, or clear the search.</div>`;
      return;
    }
    out.innerHTML = `<div class="futtablewrap"><table class="futtable">
      <thead><tr>
        <th>Contract</th><th class="r">Buy</th><th class="r">Model</th>
        <th class="r">Fair</th><th class="r">Exp. return</th><th class="r">Per yr</th>
        <th class="r">Settles</th>
      </tr></thead><tbody>
      ${d.rows.map((r) => `<tr>
        <td>
          <div class="futtitle"><span class="sidetag ${r.side || "yes"}">${(r.side || "yes").toUpperCase()}</span> ${escapeHtml(r.label)}${r.suspect ? `<span class="futflag" title="${escapeHtml(r.disagreement ? "Our own models disagree — " + r.disagreement : "Model and market disagree so wildly that a mis-mapped team or a subtly different market definition is the likelier explanation.")}">${r.disagreement ? "models disagree" : "check"}</span>` : ""}${r.thin ? `<span class="futflag" title="Thinly quoted — you may not get filled at this price.">thin</span>` : ""}</div>
          <div class="futsub">${escapeHtml(r.sport_label)} · ${escapeHtml(r.market_label)} · trust ${r.trust}${r.trust_measured ? ` <span class="trustok" title="Weight measured on ${r.trust_n} graded results">✓ measured</span>` : ` <span class="trustno" title="This model has never been scored against graded results — the weight is a cautious default, not a track record.">unvalidated</span>`}${r.in_season === false ? " · <b>off-season</b>" : ""}</div>
        </td>
        <td class="r"><b>${r.price_cents}¢</b></td>
        <td class="r">${r.model_pct}%</td>
        <td class="r">${r.fair_pct}%</td>
        <td class="r"><span class="futapy ${evCls(r.ev_pct)}">${r.ev_pct > 0 ? "+" : ""}${r.ev_pct}%</span></td>
        <td class="r">${r.apy_pct == null ? "—" : (r.apy_pct > 0 ? "+" : "") + r.apy_pct + "%"}</td>
        <td class="r">${r.days == null ? "—" : futDays(r.days)}</td>
      </tr>`).join("")}
      </tbody></table></div>
      ${d.partial ? `<div class="small" style="margin-top:6px">⏳ Still simulating — loaded ${escapeHtml((d.loaded || []).join(", "))}. More rows will appear.</div>` : ""}
      <div class="small" style="margin-top:8px;color:var(--muted)">${escapeHtml(d.note)}</div>`;
  } catch (e) {
    out.innerHTML = `<div class="empty">Couldn't load — try again.</div>`;
  }
}

// Big APY numbers on short contracts are arithmetically true and practically
// meaningless — a 20-day hold compounded 18x assumes you survive all 18 rolls.
function futApy(r) {
  if (r.apy_pct == null) return "—";
  const v = r.apy_pct >= 1000 ? Math.round(r.apy_pct).toLocaleString() : r.apy_pct.toFixed(1);
  const cls = r.apy_pct >= 25 ? "good" : r.apy_pct >= 6 ? "ok" : "meh";
  return `<span class="futapy ${cls}">${v}%</span>${r.short_term ? `<span class="futflag" title="Short hold — the annual figure is extrapolated from a few weeks, and assumes you could repeat the trade all year and win every time.">extrap.</span>` : ""}`;
}

function futDays(d) {
  if (d >= 730) return `${(d / 365).toFixed(1)} yr`;
  if (d >= 60) return `${Math.round(d / 30.4)} mo`;
  return `${Math.round(d)} d`;
}

async function loadFutures() {
  const out = $("futOut");
  if (!out) return;
  const q = ($("futQ") || {}).value || "";
  const sort = ($("futSort") || {}).value || "best";
  const minProb = ($("futMinProb") || {}).value || "95";
  const maxDays = ($("futMaxDays") || {}).value || "";
  const minVol = ($("futMinVol") || {}).value || "";
  out.innerHTML = `<div class="empty">Scanning Kalshi's long-dated markets…</div>`;
  try {
    let u = `/api/futures?limit=80&sort=${sort}&min_prob=${minProb}`
      + `&q=${encodeURIComponent(q)}`;
    if (maxDays) u += `&max_days=${maxDays}`;
    if (minVol) u += `&min_volume=${minVol}`;
    const d = await (await fetch(u)).json();
    if (d.building) {
      // Cold start: the sweep runs server-side on a background thread. Poll.
      out.innerHTML = `<div class="empty">Scanning Kalshi's long-dated markets… this takes about half a minute the first time.</div>`;
      clearTimeout(futTimer);
      futTimer = setTimeout(loadFutures, 4000);
      return;
    }
    if (d.error) { out.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    const cnt = $("futCount");
    if (cnt) cnt.textContent = `${d.total.toLocaleString()} match · ${d.universe.toLocaleString()} tracked`;
    const tiers = $("futTiers");
    if (tiers && d.summary && d.summary.tiers) {
      tiers.innerHTML = `What's on offer, by how safe the market says it is — `
        + d.summary.tiers.map((t) =>
          `<b>${t.tier}</b> ${t.n.toLocaleString()} (median ${t.median_apy == null ? "—" : t.median_apy + "%"}/yr)`).join(" · ");
    }
    if (!d.rows.length) {
      out.innerHTML = `<div class="empty">Nothing matches. Try a lower safety floor, a longer window, or a broader search.</div>`;
      return;
    }
    out.innerHTML = `<div class="futtablewrap"><table class="futtable">
      <thead><tr>
        <th>Contract</th><th class="r">Buy</th><th class="r">Pays/yr</th>
        <th class="r">Return</th><th class="r">Settles</th>
        <th class="r">Loss risk</th><th class="r">Traded</th>
      </tr></thead><tbody>
      ${d.rows.map((r) => `<tr>
        <td>
          <div class="futtitle"><span class="sidetag ${r.side}">${r.side.toUpperCase()}</span> ${escapeHtml(r.title)}</div>
          <div class="futsub">${escapeHtml(r.subtitle || "")} <span class="futtick">${escapeHtml(r.ticker)}</span></div>
        </td>
        <td class="r"><b>${r.cost_cents}¢</b></td>
        <td class="r">${futApy(r)}</td>
        <td class="r">${r.return_pct}%</td>
        <td class="r">${futDays(r.days)}</td>
        <td class="r"><span class="futrisk t-${r.tier.replace(/ /g, "-")}">${r.loss_pct}%</span></td>
        <td class="r">${Math.round(r.volume).toLocaleString()}</td>
      </tr>`).join("")}
      </tbody></table></div>
      <div class="small" style="margin-top:8px;color:var(--muted)">${escapeHtml(d.note)}</div>`;
  } catch (e) {
    out.innerHTML = `<div class="empty">Couldn't load futures — try again.</div>`;
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
      $("tab-nba").classList.toggle("hidden", tab !== "nba");
      $("tab-nhl").classList.toggle("hidden", tab !== "nhl");
      $("tab-ufc").classList.toggle("hidden", tab !== "ufc");
      $("tab-tennis").classList.toggle("hidden", tab !== "tennis");
      $("tab-lol").classList.toggle("hidden", tab !== "lol");
      $("tab-weather").classList.toggle("hidden", tab !== "weather");
      $("tab-sim").classList.toggle("hidden", tab !== "sim");
      $("tab-combine").classList.toggle("hidden", tab !== "combine");
      $("tab-futures").classList.toggle("hidden", tab !== "futures");
      $("tab-ledger").classList.toggle("hidden", tab !== "ledger");
      if (tab === "bestbets" && !$("bbetsResults").dataset.loaded) {
        $("bbetsResults").dataset.loaded = "1";
        loadBestBets();
      }
      if (tab === "combine") loadCombineCats();
      if (tab === "futures") initFutures();
      if (tab === "sim") initSim();
      if (tab === "baseball") initBaseballTab();
      if (tab === "sports") initSportsTab();
      if (tab === "nfl") initNFL();
      if (tab === "nba") initNBA();
      if (tab === "nhl") initNHL();
      if (tab === "ufc") initUFC();
      if (tab === "tennis") initTennis();
      if (tab === "lol") initLoL();
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
      box.innerHTML = `<div class="empty">No tracked games are live right now. Any MLB, NHL, NBA, NFL, college football, or soccer game we track appears here the moment it tips off, with the live score and game clock.</div>`;
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
      // The combo maker belongs to the week board -- it builds slips out of the
      // markets on THAT slate, so it has no meaning under futures or win totals.
      $("nflComboBox").classList.toggle("hidden", s !== "week");
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
// August or not, decided ONCE here and sent explicitly on every NFL request, so
// the week board, the combo maker and the DFS builder can never disagree about
// which season type they are looking at.
let nflPreseason = (() => {
  const m = new Date().getMonth() + 1;
  return m === 8 || (m === 7 && new Date().getDate() >= 25);
})();
function nflPreQuery() { return nflPreseason ? "&preseason=1" : "&preseason=0"; }
function initNFLWeek() {
  const sel = $("nflWeek");
  if (sel && !sel.dataset.filled) {
    sel.dataset.filled = "1";
    let opts = "";
    for (let w = 1; w <= 18; w++) opts += `<option value="${w}">Week ${w}</option>`;
    sel.innerHTML = opts;
    sel.addEventListener("change", () => {
      sel.dataset.userSet = "1";   // an explicit choice beats the auto week
      _nflWeekData = null;
      $("nflWeekResults").dataset.loaded = "";
      loadNFLWeek(0);
    });
  }
  const pre = $("nflPre");
  if (pre && !pre.dataset.wired) {
    pre.dataset.wired = "1";
    pre.checked = nflPreseason;
    pre.addEventListener("change", () => {
      nflPreseason = pre.checked;
      _nflWeekData = null;
      $("nflWeekResults").dataset.loaded = "1";
      loadNFLWeek(0);
      renderNFLComboMaker();
    });
  }
  if (!$("nflWeekResults").dataset.loaded) { $("nflWeekResults").dataset.loaded = "1"; loadNFLWeek(0); }
  renderNFLComboMaker();
}
async function loadNFLWeek(attempt) {
  attempt = attempt || 0;
  // week=0 until the user touches the dropdown: the server picks the first week
  // with games still to play (week 1 is the right default a few days a year —
  // the morning after the HOF game it served one finished exhibition while the
  // whole next slate sat under week 2).
  const box = $("nflWeekResults"), sel = $("nflWeek");
  const userPicked = sel && sel.dataset.userSet;
  const wk = userPicked ? sel.value : 0;
  if (!attempt) box.innerHTML = `<div class="empty">Simulating ${userPicked ? `Week ${wk}` : "the current week"} — drive-level engine${nflPreseason ? ", anchored to Kalshi's ladder (preseason)" : " off Sleeper's matchup projections"}, priced vs live Kalshi moneylines (~10s). Auto-refreshes.</div>`;
  try {
    // Drive-engine slate first; the older ESPN closed-form board is the fallback
    // (deep offseason, Sleeper gap). Slate payloads carry engine:"drive".
    let d = null;
    try { d = await (await fetch(`/api/nfl/slate?week=${wk}${nflPreQuery()}`)).json(); } catch (e) { d = null; }
    if (d && d.week && sel && !userPicked) sel.value = d.week;  // reflect the auto pick
    if (!d || d.error || !(d.games && d.games.length)) {
      const f = await (await fetch(`/api/nfl/week?week=${wk || 1}`)).json();
      if (!(f.error) && f.games && f.games.length) { _nflWeekData = f; renderNFLWeek(); return; }
      if (attempt < 9) { setTimeout(() => loadNFLWeek(attempt + 1), 6000); return; }
      box.innerHTML = `<div class="empty">${(d && d.error) || (f && f.error) || "No games for this week."}</div>`;
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
function _nflEdgeChip(cents, edge) {
  if (cents == null) return "";
  const e = edge != null ? ` <b class="${edge >= 0 ? "ev pos" : "ev neg"}">${edge >= 0 ? "+" : ""}${edge}</b>` : "";
  return `<span class="kmkt">Kalshi <b>${cents}¢</b>${e}</span>`;
}
function nflSlateCard(g) {
  const ph = Math.round(g.p_home * 1000) / 10, pa = Math.round(g.p_away * 1000) / 10;
  const kx = g.kalshi || {};
  const ladder = (g.total_ladder || []).slice(0, 4).map((r) =>
    `<span class="chip">O${r.line} <b>${r.over_pct}%</b></span>`).join(" ");
  const sh = g.spread_ladder || {};
  const spreads = ["3", "7", "10"].map((m) =>
    `<span class="chip">${g.home} −${m === "3" ? "2.5" : m === "7" ? "6.5" : "9.5"} <b>${(sh.home || {})[m] ?? "—"}%</b></span>`).join(" ");
  const props = (g.props || []).slice(0, 6).map((p) =>
    `<li><span class="legtag">${p.stat}</span> ${p.player} ${p.stat === "anytime TD" ? "" : `${p.line}+`} <span style="color:var(--muted)">(${p.over_pct}%)</span></li>`).join("");
  let sgp = "";
  if (g.sgp && g.sgp.legs) {
    const legs = g.sgp.legs.map((l) => `<li><span class="legtag">${l.type}</span> ${l.pick} <span style="color:var(--muted)">(${l.prob_pct}%)</span></li>`).join("");
    sgp = `<details class="simdetail"><summary>🎰 Same-game parlay — joint <b>${g.sgp.combined_prob_pct}%</b> (${g.sgp.fair_payout_x}×, corr ${g.sgp.corr_delta_pct >= 0 ? "+" : ""}${g.sgp.corr_delta_pct}%)</summary><ul class="legs">${legs}</ul></details>`;
  }
  const players = (g.players || []).slice(0, 6).map((p) => {
    const bits = [];
    if (p.pass_yd != null) bits.push(`${p.pass_yd} pass`);
    if (p.rush_yd != null) bits.push(`${p.rush_yd} rush`);
    if (p.rec_yd != null) bits.push(`${p.rec_yd} rec`);
    if (p.td1_pct != null) bits.push(`TD ${p.td1_pct}%`);
    return `<span class="nfl-pl"><span class="nfl-plrole">${p.pos}</span> ${p.name} <b>${bits.join(" · ")}</b></span>`;
  }).join("");
  return `<div class="bbgame nflcard">
    <div class="top"><div>
      <div class="matchup">${g.away_name || g.away} @ ${g.home_name || g.home} <span class="small" style="color:var(--muted)">${(g.date || "").slice(0, 10)}</span></div>
      <div class="pick">Pick: <b>${g.pick.name || g.pick.team}</b> ${g.pick.pct}% · total <b>${g.exp_total}</b></div>
    </div></div>
    <div class="nfl-score"><span class="nfl-sc">${g.away} <b>${g.exp_away}</b></span><span class="nfl-scsep">—</span><span class="nfl-sc"><b>${g.exp_home}</b> ${g.home}</span></div>
    <div class="winbar"><div class="fill" style="width:${ph}%"></div>
      <div class="lbl">${g.away} ${pa}% ${_nflEdgeChip(kx.away_cents, g.edge_away)} — ${_nflEdgeChip(kx.home_cents, g.edge_home)} ${ph}% ${g.home}</div></div>
    <div class="small" style="margin:4px 0">${ladder} ${spreads}</div>
    <div class="nfl-pls">${players}</div>
    ${props ? `<details class="simdetail"><summary>📊 Top props</summary><ul class="legs">${props}</ul></details>` : ""}
    ${sgp}
  </div>`;
}
function renderNFLWeek() {
  const d = _nflWeekData; if (!d) return;
  if (d.engine === "drive") {
    $("nflWeekSummary").innerHTML = `<b>${d.n_games}</b> games · Week ${d.week} · ${(d.n_sims || 0).toLocaleString()} sims/game · <i style="color:var(--muted)">${d.note}</i>`;
    $("nflWeekResults").innerHTML = d.games.map(nflSlateCard).join("");
    return;
  }
  $("nflWeekSummary").innerHTML = `<b>${d.n}</b> games · Week ${d.week} · ratings from ${d.ratings_season} season. <i style="color:var(--muted)">${d.note}</i>`;
  $("nflWeekResults").innerHTML = d.games.map(nflGameCard).join("");
}

// ---- NFL combo maker -------------------------------------------------------
// Deliberately the same controls, the same slip renderer and the same combo
// engine as baseball's: only the candidate source differs, so a band or an
// objective means exactly what it means on the other tab.
let nflComboLegs = 3, nflComboTarget = 55, nflComboCap = 0, nflComboPayout = 0;
let nflComboObjective = "balanced", nflComboLegsMode = "prefer";
let nflComboPayoutMode = "off", nflComboConn = "or", nflComboSameGame = true;
function renderNFLComboMaker() {
  const box = $("nflComboMaker");
  if (!box) return;
  const prev = (() => { const el = $("nflComboOut"); return el ? el.innerHTML : ""; })();
  const sel = (id, opts, cur) => `<select id="${id}" style="width:auto;padding:2px 4px">`
    + opts.map(([v, l]) => `<option value="${v}"${v === cur ? " selected" : ""}>${l}</option>`).join("") + `</select>`;
  box.innerHTML = `<div class="combomaker">
    🎯 <b>Combo maker</b>${nflPreseason ? ` <span class="chip">🏟️ preseason</span>` : ""}
    <div style="margin-top:8px">each leg ≥
      <input id="nflComboTarget" type="number" min="20" max="97" value="${nflComboTarget}" style="width:54px"/>%
      and ≤ <input id="nflComboCap" type="number" min="0" max="99" value="${nflComboCap || ""}" placeholder="—" style="width:54px"/>% likely</div>
    <div class="small" style="margin-top:2px;color:var(--muted)">Leave the ceiling blank for no upper limit. Set one and each ladder walks to the line that lands in the band — Kalshi books two dozen spreads and nineteen totals a game, so there is almost always a line that fits.</div>
    <div class="small" style="margin-top:6px">goal
      ${sel("nflComboObjective", [["balanced", "⚖️ best odds that aren't -EV"], ["safe", "🛡️ likeliest, any price"], ["value", "💰 best value"]], nflComboObjective)}
    </div>
    <div class="small" style="margin-top:6px">
      ${sel("nflComboLegsMode", [["prefer", "recommend"], ["require", "require"], ["off", "off"]], nflComboLegsMode)}
      <input id="nflComboN" type="number" min="2" max="12" value="${nflComboLegs}" style="width:50px"/> legs
      &nbsp;${sel("nflComboConn", [["or", "OR"], ["and", "AND"]], nflComboConn)}&nbsp;
      ${sel("nflComboPayoutMode", [["off", "off"], ["prefer", "recommend"], ["require", "require"]], nflComboPayoutMode)}
      reach <input id="nflComboPayout" type="number" min="0" step="any" value="${nflComboPayout}" style="width:60px"/>× payout
    </div>
    <label class="small" style="display:inline-block;margin-top:6px"><input type="checkbox" id="nflComboSameGame"${nflComboSameGame ? " checked" : ""} style="width:auto"/> allow same-game parlays ${lockTag("mixed_parlay")}</label>
    <div style="margin-top:6px">
      <button class="track-mini primary-mini" onclick="buildNFLCombo()">Build</button>
      <button class="track-mini" style="margin-left:6px" onclick="buildNFLCombo(true)" title="Ignore the settings above and build the likeliest slip that still pays Kalshi's ${MAX_BET_X}× ceiling">🎰 Max bet (${MAX_BET_X}×)</button>
    </div>
    <div class="small" style="margin-top:4px">Moneylines, every booked spread and total, and${nflPreseason ? "" : " (once Kalshi lists them)"} player props are all candidates. <b>Same-game on</b> may stack correlated legs from one game; off keeps one leg per game.</div>
    <div id="nflComboOut"></div>
  </div>`;
  if (prev) { const el = $("nflComboOut"); if (el) el.innerHTML = prev; }
}
async function buildNFLCombo(maxBet) {
  const out = $("nflComboOut");
  if (!out) return;
  const num = (id, dflt) => { const v = parseFloat(($(id) || {}).value); return isNaN(v) ? dflt : v; };
  nflComboLegs = Math.max(2, Math.min(12, num("nflComboN", 3)));
  nflComboTarget = Math.max(20, Math.min(97, num("nflComboTarget", 55)));
  let cap = parseInt(($("nflComboCap") || {}).value, 10);
  nflComboCap = (isNaN(cap) || cap <= 0) ? 0 : cap;
  nflComboPayout = num("nflComboPayout", 0);
  nflComboObjective = ($("nflComboObjective") || {}).value || "balanced";
  nflComboLegsMode = ($("nflComboLegsMode") || {}).value || "prefer";
  nflComboPayoutMode = ($("nflComboPayoutMode") || {}).value || "off";
  nflComboConn = ($("nflComboConn") || {}).value || "or";
  nflComboSameGame = !!(($("nflComboSameGame") || {}).checked);
  const wk = ($("nflWeek") || {}).value || 1;
  out.innerHTML = `<div class="empty">${maxBet
    ? `Searching for the likeliest slip that pays ${MAX_BET_X}×…`
    : "Simulating the slate and searching combos…"}</div>`;
  const q = `week=${wk}${nflPreQuery()}&legs=${nflComboLegs}&target=${nflComboTarget}`
    + (nflComboCap ? `&cap=${nflComboCap}` : "")
    + `&payout=${nflComboPayout}&objective=${nflComboObjective}`
    + `&legs_mode=${nflComboLegsMode}&payout_mode=${nflComboPayoutMode}`
    + `&conn=${nflComboConn}&same_game=${nflComboSameGame ? 1 : 0}`
    + (maxBet ? "&max_bet=1" : "");
  try {
    const d = await (await fetch(`/api/nfl/parlay?${q}`)).json();
    noteMaxBetCap(d);
    if (d.error) { out.innerHTML = `<div class="empty">${escapeHtml(d.error)}</div>`; return; }
    if (!d.parlay) {
      out.innerHTML = (d.hint === "single_game_no_stack")
        ? `<div class="empty">Only <b>${d.n_games_available || 1}</b> game on this board, and <b>same-game parlays are off</b> — one leg per game can't make a multi-leg slip. Tick <b>allow same-game parlays</b>, or wait for more of the week to open.</div>`
        : (d.hint === "max_bet_unreachable")
        ? `<div class="empty">No slip on this week's board can pay <b>${d.cap_x || MAX_BET_X}×</b>. Every leg needs a real Kalshi quote behind it, and a thin board runs out of them long before the ceiling.</div>`
        : `<div class="empty">No combo fits those targets on this week's board.${nflComboCap ? " The band may be too narrow — widen it, or drop the ceiling." : " Try a lower per-leg %."}</div>`;
      return;
    }
    out.innerHTML = renderMixed(d.parlay);
  } catch (e) {
    out.innerHTML = `<div class="empty">Combo build failed.</div>`;
  }
}

// ---- Basketball slate cards (shared renderer) -----------------------------
// These came in with the WNBA board and outlived it: the NBA slate renders
// through exactly the same card, so they are named for the sport rather than
// the league that happened to introduce them.
function _basketEdges(rows, kind) {
  if (!rows || !rows.length) return "";
  const chips = rows.slice(0, 4).map((r) => {
    const lbl = kind === "total" ? `O${r.line}` : `${r.team} −${r.line}`;
    return `<span class="chip">${lbl} <b>${r.model_pct}%</b> vs ${r.cents}¢ <b class="${r.edge >= 0 ? "ev pos" : "ev neg"}">${r.edge >= 0 ? "+" : ""}${r.edge}</b></span>`;
  }).join(" ");
  return `<div class="small" style="margin:3px 0">${kind === "total" ? "Totals" : "Spreads"} (Kalshi lines): ${chips}</div>`;
}
function basketGameCard(g) {
  const ph = Math.round(g.p_home * 1000) / 10, pa = Math.round(g.p_away * 1000) / 10;
  const kx = g.kalshi || {};
  const live = g.state === "in" ? `<span style="color:#e0566a">🔴 ${g.detail || "live"} · ${g.away} ${g.away_score}–${g.home_score} ${g.home}</span> · ` : "";
  const done = g.state === "post" ? `<b>Final: ${g.away} ${g.away_score}–${g.home_score} ${g.home}</b> · ` : "";
  const players = (g.players || []).slice(0, 6).map((p) => {
    const bits = [`${p.pts} pts`];
    if (p.reb != null) bits.push(`${p.reb} reb`);
    if (p.ast != null) bits.push(`${p.ast} ast`);
    return `<span class="nfl-pl"><span class="nfl-plrole">${p.team}</span> ${p.name} <b>${bits.join(" · ")}</b></span>`;
  }).join("");
  const props = (g.props || []).slice(0, 6).map((p) =>
    `<li><span class="legtag">${p.stat}</span> ${p.player} ${p.line}+ <span style="color:var(--muted)">(${p.over_pct}%)</span></li>`).join("");
  let sgp = "";
  if (g.sgp && g.sgp.legs) {
    const legs = g.sgp.legs.map((l) => `<li><span class="legtag">${l.type}</span> ${l.pick} <span style="color:var(--muted)">(${l.prob_pct}%)</span></li>`).join("");
    sgp = `<details class="simdetail"><summary>🎰 Same-game parlay — joint <b>${g.sgp.combined_prob_pct}%</b> (${g.sgp.fair_payout_x}×, corr ${g.sgp.corr_delta_pct >= 0 ? "+" : ""}${g.sgp.corr_delta_pct}%)</summary><ul class="legs">${legs}</ul></details>`;
  }
  return `<div class="bbgame nflcard">
    <div class="top"><div>
      <div class="matchup">${g.away_name || g.away} @ ${g.home_name || g.home} <span class="small" style="color:var(--muted)">${(g.date || "").slice(11, 16)}Z</span></div>
      <div class="pick">${done}${live}Pick: <b>${g.pick.name || g.pick.team}</b> ${g.pick.pct}% · total <b>${g.exp_total}</b> · recs ${g.away_rec} / ${g.home_rec}</div>
    </div></div>
    <div class="nfl-score"><span class="nfl-sc">${g.away} <b>${g.exp_away}</b></span><span class="nfl-scsep">—</span><span class="nfl-sc"><b>${g.exp_home}</b> ${g.home}</span></div>
    <div class="winbar"><div class="fill" style="width:${ph}%"></div>
      <div class="lbl">${g.away} ${pa}% ${_nflEdgeChip(kx.away_cents, g.edge_away)} — ${_nflEdgeChip(kx.home_cents, g.edge_home)} ${ph}% ${g.home}</div></div>
    ${_basketEdges(g.spread_edges, "spread")}
    ${_basketEdges(g.total_edges, "total")}
    <div class="nfl-pls">${players}</div>
    ${props ? `<details class="simdetail"><summary>📊 Top props (points / rebounds / assists)</summary><ul class="legs">${props}</ul></details>` : ""}
    ${sgp}
  </div>`;
}
// ---- NBA possession-engine slate (shares the basketball card renderer) ------
let _nbaData = null;
function initNBA() {
  const dt = $("nbaDate");
  if (dt && !dt.dataset.wired) {
    dt.dataset.wired = "1";
    dt.value = new Date().toISOString().slice(0, 10);
    dt.addEventListener("change", () => { _nbaData = null; $("nbaResults").dataset.loaded = ""; loadNBA(0); });
  }
  if (!$("nbaResults").dataset.loaded) { $("nbaResults").dataset.loaded = "1"; loadNBA(0); }
}
async function loadNBA(attempt) {
  attempt = attempt || 0;
  const box = $("nbaResults"), dt = ($("nbaDate") || {}).value || "";
  if (!attempt) box.innerHTML = `<div class="empty">Simulating the slate — possession engine + live Kalshi ML/spread/total pricing (~5s). Auto-refreshes.</div>`;
  try {
    const d = await (await fetch(`/api/nba/slate${dt ? `?date=${dt}` : ""}`)).json();
    if (d.error) {
      if (attempt < 9) { setTimeout(() => loadNBA(attempt + 1), 5000); return; }
      box.innerHTML = `<div class="empty">${d.error}</div>`;
      return;
    }
    _nbaData = d;
    renderNBA();
  } catch (e) {
    if (attempt < 9) { setTimeout(() => loadNBA(attempt + 1), 5000); return; }
    box.innerHTML = `<div class="empty">NBA slate unavailable.</div>`;
  }
}
function renderNBA() {
  const d = _nbaData; if (!d) return;
  if (!d.games || !d.games.length) {
    $("nbaSummary").innerHTML = "";
    $("nbaResults").innerHTML = `<div class="empty">${d.note || "No NBA games for this date."}</div>`;
    return;
  }
  $("nbaSummary").innerHTML = `<b>${d.n_games}</b> games · ${(d.n_sims || 0).toLocaleString()} sims/game · <i style="color:var(--muted)">${d.note}</i>`;
  $("nbaResults").innerHTML = d.games.map(basketGameCard).join("");
}

// ---- NHL shot-event slate --------------------------------------------------
let _nhlData = null;
function initNHL() {
  const dt = $("nhlDate");
  if (dt && !dt.dataset.wired) {
    dt.dataset.wired = "1";
    dt.value = new Date().toISOString().slice(0, 10);
    dt.addEventListener("change", () => { _nhlData = null; $("nhlResults").dataset.loaded = ""; loadNHL(0); });
  }
  if (!$("nhlResults").dataset.loaded) { $("nhlResults").dataset.loaded = "1"; loadNHL(0); }
}
async function loadNHL(attempt) {
  attempt = attempt || 0;
  const box = $("nhlResults"), dt = ($("nhlDate") || {}).value || "";
  if (!attempt) box.innerHTML = `<div class="empty">Simulating the slate — shot-event engine + live Kalshi ML/spread/total pricing (~5s). Auto-refreshes.</div>`;
  try {
    const d = await (await fetch(`/api/nhl/slate${dt ? `?date=${dt}` : ""}`)).json();
    if (d.error) {
      if (attempt < 9) { setTimeout(() => loadNHL(attempt + 1), 5000); return; }
      box.innerHTML = `<div class="empty">${d.error}</div>`;
      return;
    }
    _nhlData = d;
    renderNHL();
  } catch (e) {
    if (attempt < 9) { setTimeout(() => loadNHL(attempt + 1), 5000); return; }
    box.innerHTML = `<div class="empty">NHL slate unavailable.</div>`;
  }
}
function nhlGameCard(g) {
  const ph = Math.round(g.p_home * 1000) / 10, pa = Math.round(g.p_away * 1000) / 10;
  const kx = g.kalshi || {};
  const live = g.state === "in" ? `<span style="color:#e0566a">🔴 ${g.detail || "live"} · ${g.away} ${g.away_score}–${g.home_score} ${g.home}</span> · ` : "";
  const done = g.state === "post" ? `<b>Final: ${g.away} ${g.away_score}–${g.home_score} ${g.home}</b> · ` : "";
  const players = (g.players || []).slice(0, 6).map((p) => {
    const bits = [];
    if (p.goals != null) bits.push(`${p.goals} G`);
    if (p.pts != null) bits.push(`${p.pts} P`);
    return `<span class="nfl-pl"><span class="nfl-plrole">${p.team}</span> ${p.name} <b>${bits.join(" · ")}</b></span>`;
  }).join("");
  const props = (g.props || []).slice(0, 6).map((p) =>
    `<li><span class="legtag">${p.stat}</span> ${p.player} <span style="color:var(--muted)">(${p.over_pct}%)</span></li>`).join("");
  let sgp = "";
  if (g.sgp && g.sgp.legs) {
    const legs = g.sgp.legs.map((l) => `<li><span class="legtag">${l.type}</span> ${l.pick} <span style="color:var(--muted)">(${l.prob_pct}%)</span></li>`).join("");
    sgp = `<details class="simdetail"><summary>🎰 Same-game parlay — joint <b>${g.sgp.combined_prob_pct}%</b> (${g.sgp.fair_payout_x}×, corr ${g.sgp.corr_delta_pct >= 0 ? "+" : ""}${g.sgp.corr_delta_pct}%)</summary><ul class="legs">${legs}</ul></details>`;
  }
  return `<div class="bbgame nflcard">
    <div class="top"><div>
      <div class="matchup">${g.away_name || g.away} @ ${g.home_name || g.home} <span class="small" style="color:var(--muted)">${(g.date || "").slice(11, 16)}Z</span></div>
      <div class="pick">${done}${live}Pick: <b>${g.pick.name || g.pick.team}</b> ${g.pick.pct}% · total goals <b>${g.exp_total}</b></div>
    </div></div>
    <div class="nfl-score"><span class="nfl-sc">${g.away} <b>${g.exp_away}</b></span><span class="nfl-scsep">—</span><span class="nfl-sc"><b>${g.exp_home}</b> ${g.home}</span></div>
    <div class="winbar"><div class="fill" style="width:${ph}%"></div>
      <div class="lbl">${g.away} ${pa}% ${_nflEdgeChip(kx.away_cents, g.edge_away)} — ${_nflEdgeChip(kx.home_cents, g.edge_home)} ${ph}% ${g.home}</div></div>
    ${_basketEdges(g.spread_edges, "spread")}
    ${_basketEdges(g.total_edges, "total")}
    <div class="nfl-pls">${players}</div>
    ${props ? `<details class="simdetail"><summary>📊 Top props (anytime goal / 1+ point)</summary><ul class="legs">${props}</ul></details>` : ""}
    ${sgp}
  </div>`;
}
function renderNHL() {
  const d = _nhlData; if (!d) return;
  if (!d.games || !d.games.length) {
    $("nhlSummary").innerHTML = "";
    $("nhlResults").innerHTML = `<div class="empty">${d.note || "No NHL games for this date."}</div>`;
    return;
  }
  $("nhlSummary").innerHTML = `<b>${d.n_games}</b> games · ${(d.n_sims || 0).toLocaleString()} sims/game · <i style="color:var(--muted)">${d.note}</i>`;
  $("nhlResults").innerHTML = d.games.map(nhlGameCard).join("");
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
  // Same flag the week board and the combo maker use, so the three views of one
  // slate can never end up looking at different season types.
  const pre = $("nflSimPre");
  if (pre && !pre.dataset.wired) {
    pre.dataset.wired = "1";
    pre.addEventListener("change", () => {
      nflPreseason = pre.checked;
      const wk = $("nflPre"); if (wk) wk.checked = pre.checked;
      _nflSimData = null; _nflWeekData = null;
      $("nflSimResults").dataset.loaded = "1";
      $("nflWeekResults").dataset.loaded = "";
      loadNFLSim(0);
      renderNFLComboMaker();
    });
  }
  if (pre) pre.checked = nflPreseason;
  if (_nflSimData) { renderNFLSim(); return; }
  if (!$("nflSimResults").dataset.loaded) { $("nflSimResults").dataset.loaded = "1"; loadNFLSim(0); }
}
async function loadNFLSim(attempt) {
  attempt = attempt || 0;
  const box = $("nflSimResults"), wk = ($("nflSimWeek") || {}).value || 1;
  if (!attempt) box.innerHTML = `<div class="empty">Simulating Week ${wk} in the background — ${nflPreseason ? "measured preseason usage" : "Sleeper projections"} + correlated game sims (~10s). Auto-refreshes.</div>`;
  try {
    const d = await (await fetch(`/api/nfl/sim?week=${wk}${nflPreQuery()}`)).json();
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
  // Our pick disagrees with the book on who wins — the picks most likely to be
  // model error rather than edge, so say so on the card instead of hiding it.
  const fade = f.fades_market
    ? ` <span class="ufc-debut" title="Our model favours this fighter but the market has him as the underdog. Disagreeing with a liquid market is where model error shows up first — treat as a flag, not an edge, until the model has graded results behind it.">⚠️ fades the book</span>` : "";
  return `<div class="ufc-fighter">
      <div class="ufc-fname"><b>${f.name}</b>${_ratingChip(f)}${fade} <span class="small" style="color:var(--muted)">${recTxt}</span></div>
      <div class="ufc-fnums"><span class="ufc-win">${f.win_pct}%${fair}</span>
        <span class="fr-num">${px}</span><span class="fr-num">${_ufcEdge(f.edge)}</span>
        <span class="fr-num" title="DraftKings projection / ceiling">DK ${f.proj}<span style="color:var(--muted)">/${f.ceil}</span></span></div>
    </div>`;
}
function renderUFC() {
  const d = _ufcData; if (!d) return;
  $("ufcSummary").innerHTML = `<b>${d.event || "Upcoming card"}</b>${d.date ? " · " + d.date : ""} · ${d.bouts.length} bouts · model = ratings from each fighter's past fights → win prob, method/round & DK points. <span class="ufc-rating" style="border-color:var(--muted);color:var(--muted)">⚡</span> = our 0-100 power rating (league avg 50; hover for the striking/grappling/finishing breakdown). ⚠️ flags fighters with no/thin UFC history running on a league-average baseline. Edge = our fair win% (blended toward the market when history is thin) − Kalshi ask.`;
  appendCalNote("ufcSummary", "ufc", "UFC");
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
let _tnData = null, _tnLivePoll = null, _tnPoll = null, _tnSub = "combo";
function initTennis() {
  if (!$("tnResults").dataset.loaded) {
    $("tnResults").dataset.loaded = "1";
    loadTennis();
    $("tnSearch")?.addEventListener("input", () => renderTennis());
    const ss = $("tnSort");
    if (ss && !ss.dataset.filled) {
      ss.dataset.filled = "1";
      ss.innerHTML = _TN_SORTS.map(([v, l]) =>
        `<option value="${v}"${v === _tnSort ? " selected" : ""}>${l}</option>`).join("");
    }
    document.querySelectorAll("#tnSubtabs .subtab").forEach((b) => {
      b.addEventListener("click", () => {
        document.querySelectorAll("#tnSubtabs .subtab").forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
        _tnSub = b.dataset.tnsub;
        // The maker replaces the board rather than sitting under it: it is a
        // different job, and 264 cards above it is exactly the scrolling this
        // change exists to end.
        const mk = _tnSub === "maker";
        $("tnMakerBox").classList.toggle("hidden", !mk);
        $("tnResults").classList.toggle("hidden", mk);
        $("tnSearchRow").classList.toggle("hidden", mk);
        if (mk) { renderTennisMaker(); return; }
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
// Surface, said honestly. A court we could not identify is shown as unknown
// rather than guessed, because the model genuinely does not use one there — it
// falls back to the player's surface-agnostic profile. Silently printing a
// surface we are not modelling on would be the misleading option.
function surfTag(m) {
  if (m.surface_known === false || !m.surface || m.surface === "Unknown") {
    return `<span class="tn-surfunk" title="We could not identify this tournament's court, so the model uses each player's overall (surface-agnostic) profile rather than guessing a surface.">surface unknown</span>`;
  }
  return m.surface;
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
// ---- Tennis combo maker ----------------------------------------------------
// Same controls and the same slip renderer as the NFL and baseball makers. What
// differs is underneath: tennis has no bitmask sim to stack correlated legs, so
// this runs through combine's assembler with the category pinned to tennis, and
// the joint probability is an honest product of independent matches.
let tnComboLegs = 3, tnComboTarget = 60, tnComboCap = 0, tnComboPayout = 0;
let tnComboLive = false, tnComboWindow = "";
let _tnWinCounts = null;
// Leg types, mirroring combine.CATEGORY_TYPES["tennis"]. The cross-sport maker
// has always had these chips; the dedicated tennis maker shipped without them,
// so asking for "just match winners" was impossible and a slip came back as two
// straight-sets legs, a total-games line and one match winner.
const _TN_TYPES = [["Match", "Match winner"], ["Sets", "Sets / straight-sets"],
                   ["Games", "Total games"], ["Aces", "Total aces"]];
let _tnTypes = new Set();
window.toggleTnType = (el, t) => {
  if (_tnTypes.has(t)) { _tnTypes.delete(t); el.classList.remove("on"); }
  else { _tnTypes.add(t); el.classList.add("on"); }
};
function tnTypesParam() {
  return _tnTypes.size ? "&types=" + [..._tnTypes].map(encodeURIComponent).join(",") : "";
}
let tnComboLegsMode = "prefer", tnComboPayoutMode = "off", tnComboConn = "or";
function renderTennisMaker() {
  const box = $("tnMaker");
  if (!box) return;
  const prev = (() => { const el = $("tnComboOut"); return el ? el.innerHTML : ""; })();
  const n = (_tnData && _tnData.n_combo != null) ? _tnData.n_combo : null;
  const sel = (id, opts, cur) => `<select id="${id}" style="width:auto;padding:2px 4px">`
    + opts.map(([v, l]) => `<option value="${v}"${v === cur ? " selected" : ""}>${l}</option>`).join("") + `</select>`;
  box.innerHTML = `<div class="combomaker">
    🎯 <b>Tennis combo maker</b>
    ${n != null ? `<div class="small" style="margin:4px 0 2px">Drawing from the <b>${n}</b> match${n === 1 ? "" : "es"} Kalshi has actually opened for combos and that have a real book. <b>ITF counts</b> — eligibility is per match, not per tour, and Kalshi publishes it.</div>` : ""}
    <div style="margin-top:8px">each leg ≥
      <input id="tnComboTarget" type="number" min="20" max="97" value="${tnComboTarget}" style="width:54px"/>%
      and ≤ <input id="tnComboCap" type="number" min="0" max="99" value="${tnComboCap || ""}" placeholder="—" style="width:54px"/>% likely</div>
    <div class="small" style="margin-top:2px;color:var(--muted)">Set a ceiling. Without one a live board hands you three matches that are already decided — 97-99¢ legs, 91% combined, paying 1.1×, all technically true and none of them a bet you want.</div>
    <div class="small" style="margin-top:6px">
      ${sel("tnComboLegsMode", [["prefer", "recommend"], ["require", "require"], ["off", "off"]], tnComboLegsMode)}
      <input id="tnComboN" type="number" min="2" max="12" value="${tnComboLegs}" style="width:50px"/> legs
      &nbsp;${sel("tnComboConn", [["or", "OR"], ["and", "AND"]], tnComboConn)}&nbsp;
      ${sel("tnComboPayoutMode", [["off", "off"], ["prefer", "recommend"], ["require", "require"]], tnComboPayoutMode)}
      reach <input id="tnComboPayout" type="number" min="0" step="any" value="${tnComboPayout}" style="width:60px"/>× payout
    </div>
    <div class="small" style="margin-top:8px">Starts
      ${sel("tnComboWindow", [["", "any time" + (_tnWinCounts ? ` (${_tnWinCounts.any})` : "")],
                              ["today", "today only" + (_tnWinCounts ? ` (${_tnWinCounts.today})` : "")],
                              ["3h", "within 3 hours" + (_tnWinCounts ? ` (${_tnWinCounts["3h"]})` : "")],
                              ["1h", "within the hour" + (_tnWinCounts ? ` (${_tnWinCounts["1h"]})` : "")]], tnComboWindow)}
    </div>
    <div class="small" style="margin-top:2px;color:var(--muted)"><b>today only</b> is enforceable for every match — the day is in Kalshi's own event ticker. The hour windows need a published start time, which is ATP/WTA only${_tnWinCounts && _tnWinCounts.no_clock ? `, so <b>${_tnWinCounts.no_clock}</b> otherwise-eligible matches can't qualify for one` : ""}. Kalshi's own timing field was checked as a substitute and is off by anywhere from 28 hours early to 15 hours late.</div>
    <div class="small" style="margin-top:8px">Leg types <span style="color:var(--muted)">(none selected = all)</span>: <span class="ptchips">${_TN_TYPES.map(([v, l]) => `<span class="ptchip${_tnTypes.has(v) ? " on" : ""}" onclick="toggleTnType(this,'${v}')">${l}</span>`).join("")}</span></div>
    <label class="small" style="display:block;margin-top:8px"><input type="checkbox" id="tnComboLive"${tnComboLive ? " checked" : ""} style="width:auto"/> 🔴 include matches already on court</label>
    <div class="small" style="margin-top:2px;color:var(--muted)">Off by default: a match in progress is priced off a score we may be seconds behind, while our win% is a pre-match read. ITF has no scoreboard anywhere — ESPN publishes ATP/WTA only — so those are detected from <b>Kalshi's own trade tape</b> instead: a match being played trades continuously (its last 40 trades span a minute or two) and a scheduled one does not (half an hour to a day).</div>
    <div style="margin-top:8px">
      <button class="track-mini primary-mini" onclick="buildTennisCombo()">Build</button>
      <button class="track-mini" style="margin-left:6px" onclick="buildTennisCombo(true)" title="Ignore the settings above and build the likeliest slip that still pays Kalshi's ${MAX_BET_X}× ceiling">🎰 Max bet (${MAX_BET_X}×)</button>
    </div>
    <div class="small" style="margin-top:4px">Match winners plus the derived markets the same simulation prices — total games, straight sets, aces — so a slip stays internally consistent. Pick <b>Match winner</b> alone for a plain winners-only parlay.</div>
    <div id="tnComboOut"></div>
  </div>`;
  if (prev) { const el = $("tnComboOut"); if (el) el.innerHTML = prev; }
}
async function buildTennisCombo(maxBet) {
  // Resolve the output node at WRITE time, every time. Capturing it once was the
  // bug behind "Building…" forever: renderTennisMaker() below rebuilds the whole
  // maker box to refresh the window counts, which destroys and recreates
  // #tnComboOut -- so the captured reference became a detached node and every
  // later write landed nowhere, while the rebuild restored the preserved
  // "Building…" into the new one.
  const put = (html) => { const el = $("tnComboOut"); if (el) el.innerHTML = html; };
  if (!$("tnComboOut")) return;
  const num = (id, d) => { const v = parseFloat(($(id) || {}).value); return isNaN(v) ? d : v; };
  tnComboLegs = Math.max(2, Math.min(12, num("tnComboN", 3)));
  tnComboTarget = Math.max(20, Math.min(97, num("tnComboTarget", 60)));
  let _tc = parseInt(($("tnComboCap") || {}).value, 10);
  tnComboCap = (isNaN(_tc) || _tc <= 0) ? 0 : _tc;
  tnComboPayout = num("tnComboPayout", 0);
  tnComboLegsMode = ($("tnComboLegsMode") || {}).value || "prefer";
  tnComboPayoutMode = ($("tnComboPayoutMode") || {}).value || "off";
  tnComboConn = ($("tnComboConn") || {}).value || "or";
  tnComboLive = !!(($("tnComboLive") || {}).checked);
  tnComboWindow = ($("tnComboWindow") || {}).value || "";
  put(`<div class="empty">${maxBet
    ? `Searching for the likeliest slip that pays ${MAX_BET_X}×…` : "Building…"}</div>`);
  const q = `legs=${tnComboLegs}&target=${tnComboTarget}&payout=${tnComboPayout}`
    + (tnComboCap ? `&cap=${tnComboCap}` : "") + (tnComboLive ? "&live=1" : "")
    + tnTypesParam() + (tnComboWindow ? `&window=${tnComboWindow}` : "")
    + `&legs_mode=${tnComboLegsMode}&payout_mode=${tnComboPayoutMode}&conn=${tnComboConn}`
    + (maxBet ? "&max_bet=1" : "");
  try {
    const d = await (await fetch(`/api/tennis/parlay?${q}`)).json();
    noteMaxBetCap(d);
    if (d.window_counts) { _tnWinCounts = d.window_counts; renderTennisMaker(); }
    if (d.error) { put(`<div class="empty">${escapeHtml(d.error)}</div>`); return; }
    if (!d.combo) {
      const n = d.n_combo_matches;
      if (d.hint === "max_bet_unreachable" || d.hint === "max_bet_needs_priced_legs") {
        put(`<div class="empty">No tennis slip can pay <b>${d.cap_x || MAX_BET_X}×</b> right now. A max bet only uses matches with a real, traded Kalshi quote on both sides — most of the board is listed but barely quoted, so there usually aren't enough to multiply that far.</div>`);
        return;
      }
      put(`<div class="empty">No slip fits those targets.${n === 0
        ? " Kalshi has no tennis match open for combos right now."
        : (n != null ? ` Only <b>${n}</b> match${n === 1 ? " is" : "es are"} eligible today, so try fewer legs or a lower per-leg %.` : "")}</div>`);
      return;
    }
    put(renderCombo(d.combo, maxBet ? `🎰 Tennis max bet` : "🎾 Tennis parlay", "hl prop"));
  } catch (e) {
    put(`<div class="empty">Build failed.</div>`);
  }
}

// The DAY a tennis match is played, which is in the Kalshi event ticker and so
// known for every match including all of ITF -- unlike the clock time, which
// only ESPN publishes and only for ATP/WTA. Showing it is the fix for betting a
// match you believed was today.
function tnDayTag(m) {
  const ds = m.date || "";
  if (ds.length !== 8) return "";
  const y = +ds.slice(0, 4), mo = +ds.slice(4, 6), d = +ds.slice(6, 8);
  const when = new Date(y, mo - 1, d);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const days = Math.round((when - today) / 86400000);
  if (days === 0) return `<span class="tn-day today" title="playing today">Today</span>`;
  if (days === 1) return `<span class="tn-day soon" title="NOT today — tomorrow">Tomorrow</span>`;
  if (days < 0) return `<span class="tn-day past" title="scheduled before today">${when.toLocaleDateString([], { month: "short", day: "numeric" })}</span>`;
  return `<span class="tn-day soon" title="${days} days out">${when.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })}</span>`;
}
// Surface, ordered so the courts we actually model come first and the ones we
// could not identify sort last rather than scattering through the list.
const _TN_SURF_ORDER = { "Clay": 0, "Hard": 1, "Grass": 2, "Carpet": 3 };
function _tnSurfKey(m) {
  if (m.surface_known === false || !m.surface || m.surface === "Unknown") return 9;
  return _TN_SURF_ORDER[m.surface] != null ? _TN_SURF_ORDER[m.surface] : 8;
}
function _tnBestEdge(m) {
  return Math.max(m.a.edge == null ? -99 : m.a.edge, m.b.edge == null ? -99 : m.b.edge);
}
function _tnStartMs(m) {
  if (m.start) { const t = new Date(m.start).getTime(); if (!isNaN(t)) return t; }
  // No clock: fall back to the DAY so a dateless sort still puts tomorrow after
  // today instead of dumping every ITF match in one indistinguishable block.
  const ds = m.date || "";
  if (ds.length === 8) return new Date(+ds.slice(0,4), +ds.slice(4,6)-1, +ds.slice(6,8), 23, 59).getTime();
  return Infinity;
}
let _tnSort = "default";
const _TN_SORTS = [["default", "Best read first"], ["edge", "Biggest edge"],
                   ["start", "Starting soonest"], ["surface", "Court surface"],
                   ["tour", "Tour"], ["conf", "Most charted data"]];
function tnApplySort(matches) {
  const t = { high: 0, medium: 1, elo: 2, thin: 3, market: 4 };
  const by = {
    edge: (x, y) => _tnBestEdge(y) - _tnBestEdge(x),
    start: (x, y) => _tnStartMs(x) - _tnStartMs(y),
    surface: (x, y) => _tnSurfKey(x) - _tnSurfKey(y) || _tnBestEdge(y) - _tnBestEdge(x),
    tour: (x, y) => String(x.tour).localeCompare(String(y.tour)) || _tnBestEdge(y) - _tnBestEdge(x),
    conf: (x, y) => (t[x.conf_tier] ?? 9) - (t[y.conf_tier] ?? 9) || _tnBestEdge(y) - _tnBestEdge(x),
  }[_tnSort];
  // Live always floats to the top whatever the sort: it is the only thing on the
  // board with a clock running on it.
  return by ? matches.slice().sort((x, y) =>
    (y.live ? 1 : 0) - (x.live ? 1 : 0) || by(x, y)) : matches;
}
window.setTnSort = (v) => { _tnSort = v; renderTennis(); };
// Live scores, the trade tape and Kalshi's prices all move inside the board's
// own cache window, so a manual pull is the difference between reading a dip and
// reading a dip from four minutes ago.
window.refreshTennis = async (btn) => {
  if (btn) { btn.disabled = true; btn.textContent = "↻ …"; }
  try { await loadTennis(); }
  finally { if (btn) { btn.disabled = false; btn.textContent = "↻ Refresh"; } }
};

function renderTennis() {
  const d = _tnData; if (!d) return;
  let matches = (d.matches || []).slice();
  // The default view. A 264-match board where ~40 can go in a slip is not a
  // board, it's a haystack — Kalshi refuses ITF as a parlay leg, and plenty of
  // what's left is listed without being quoted.
  if (_tnSub === "combo") matches = matches.filter((m) => m.combo_ok);
  else if (_tnSub === "atp") matches = matches.filter((m) => m.tour === "ATP");
  else if (_tnSub === "wta") matches = matches.filter((m) => m.tour === "WTA");
  else if (_tnSub === "itf") matches = matches.filter((m) => (m.tour || "").startsWith("ITF"));
  // Edges EXCLUDES anything already on court. Our number is a PRE-MATCH read and
  // the price is live, so the gap between them is staleness, not insight: a
  // player we had at 60% whose price has crashed to 20c because he is a set down
  // shows up as a +40 "edge" and tops the tab. The upset radar is where an
  // in-progress swing belongs, and it re-simulates from the current score.
  else if (_tnSub === "edges") matches = matches.filter((m) =>
    !m.live && [m.a.edge, m.b.edge].some((e) => e != null && e >= 4));
  else if (_tnSub === "live") {
    matches = matches.filter((m) => m.live);
    matches.sort((x, y) => (y.upset_score || 0) - (x.upset_score || 0));
  } else if (_tnSub === "upsets") {
    matches = matches.filter((m) => m.upset);
    matches.sort((x, y) => (y.upset_score || 0) - (x.upset_score || 0));
  } else if (_tnSub === "dips") {
    matches = matches.filter((m) => m.dip);
    // Verified dips first — dip_score is deliberately deeply negative for the
    // unverified ones, so a bigger unknown can never outrank a real edge.
    matches.sort((x, y) => (y.dip_score || 0) - (x.dip_score || 0));
  }
  const q = ($("tnSearch")?.value || "").trim().toLowerCase();
  if (q) matches = matches.filter((m) =>
    (m.a.name + " " + m.b.name + " " + (m.tour || "") + " " + (m.tournament || "")
      + " " + (m.kalshi_series || "") + " " + ((m.live || {}).tournament || ""))
      .toLowerCase().includes(q));
  matches = tnApplySort(matches);
  const liveBit = d.n_live ? ` · <b style="color:#e5484d">🔴 ${d.n_live} live</b>` : "";
  const upBit = d.n_upsets ? ` · <b style="color:#e5484d">🚨 ${d.n_upsets} favorite${d.n_upsets > 1 ? "s" : ""} trailing</b>` : "";
  const playBit = d.n_play != null && d.n_play < d.n_matches ? ` <span class="small" style="color:var(--muted)">(${d.n_play} with a model read, rest are markets not open yet)</span>` : "";
  const comboBit = d.n_combo != null
    ? ` · <b style="color:#3ad17a">🎲 ${d.n_combo} combo-ready</b>` : "";
  const dipBit = d.n_dips ? ` · <b style="color:#3ad17a">📉 ${d.n_dips} dip${d.n_dips > 1 ? "s" : ""}</b>` : "";
  $("tnSummary").innerHTML = _tnSub === "dips"
    ? `<b>${matches.length}</b> match${matches.length === 1 ? "" : "es"} where the market has marked a favourite down mid-match. A <b>verified</b> dip re-runs the point-by-point sim <b>from the current score</b> and is only shown when that number still beats the ask by ${8}+ — a real edge on a number we computed. An <b>unverified</b> one is in play on a tour with no scoreboard, so the price drop is the only thing we can see, and a collapse looks identical to a comeback from here: those are listed last and are not a recommendation. <b>Hit ↻ Refresh before acting</b> — a live price moves while you read it.`
    : _tnSub === "combo"
    ? `<b>${matches.length} of ${d.n_matches}</b> matches can actually be a <b>parlay leg</b>${liveBit}${upBit}. Kalshi decides this <b>per match</b> — not per tour, so plenty of <b>ITF</b> qualifies — and it also needs a <b>real book</b>, not just a listed price. Each card that missed says which. Everything else is one click away under <b>All</b>, where it's fine as a single bet. Edge = fair win% − Kalshi ask.`
    : `<b>${d.n_matches} matches</b>${comboBit}${liveBit}${upBit}${dipBit}${playBit}. Model = serve/return rates from charted matches → point-by-point sim (with <b>recent-match fatigue</b>), <b>ensembled with our own Elo</b>. Each card shows <b>where to find it on Kalshi</b> (series + tournament). A heavy favorite still loses sometimes — the <b>1-in-N</b> tag is the real single-match upset rate. The green <b>✅ Lean</b> is the side to look at. Edge = fair win% − Kalshi ask.`;
  appendCalNote("tnSummary", "tennis", "tennis");
  if (!matches.length) {
    const msg = _tnSub === "live" ? "No tracked matches on court right now."
      : _tnSub === "upsets" ? "No big favorites trailing right now — check back during play."
      : _tnSub === "dips" ? "No favourite is being marked down right now. This fills up mid-match when a big name drops a set and the price overshoots."
      : _tnSub === "combo" ? "No match on the board can be a parlay leg right now — Kalshi only takes ATP/WTA in a slip, and none are quoted. Check <b>All</b> for singles."
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
    // A tape-detected live match has NO score -- that is the honest limit of
    // reading "in play" off trade velocity -- so the chip must not render one.
    // It was printing "LIVE undefined-undefined".
    const liveChip = lv
      ? (lv.sets_a != null
        ? `<span class="tn-livechip">🔴 LIVE ${lv.sets_a}–${lv.sets_b}${lv.cur ? ` (${lv.cur[0]}-${lv.cur[1]})` : ""} · ${lv.detail || lv.score}</span>`
        : `<span class="tn-livechip" title="in play, detected from Kalshi's trade tape — no scoreboard covers this tour, so we have no score">🔴 LIVE · in play<span style="opacity:.7"> (no score feed)</span></span>`)
      : "";
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
      ? (up.price_only
        ? `<div class="tn-upset">🚨 <b>${up.fav}</b> ${up.note} — in play with no scoreboard for this tour, so the price drop is all we can see. Worth a look, not a read.</div>`
        : `<div class="tn-upset">🚨 <b>${up.fav}</b> is ${up.note}${up.sets ? ` (sets ${up.sets}` : " ("}${up.fav_cents != null ? ` · ${up.fav_cents}¢` : ""}) but ${liveNote}.</div>`)
      : "";
    const dp = m.dip;
    const dipBanner = dp
      ? (dp.tier === "verified"
        ? `<div class="tn-dip verified" title="the sim re-run from the CURRENT score still has this player ahead of what the market is charging">📉 <b>Dip:</b> <b>${dp.player}</b> is <b>${dp.cents}¢</b> but the sim run from the current score${dp.sets ? ` (${dp.sets})` : ""} still has him <b>${dp.live_pct}%</b> — <span class="ev pos">+${dp.edge}</span>. Pre-match read was ${dp.model_pct}%.</div>`
        : `<div class="tn-dip unverified" title="in play with no scoreboard for this tour — the price drop is the only observation we have">⚠️ <b>Unverified dip:</b> <b>${dp.player}</b> has fallen to <b>${dp.cents}¢</b> from a ${dp.model_pct}% pre-match read (−${dp.drop}). <b>No score feed for this tour</b>, so we cannot tell a comeback from a collapse — this is a place to look, not a number.</div>`)
      : "";
    const unopened = m.tier === "unopened";
    const leanBlock = unopened
      ? `<div class="tn-lean none">⚪ Market not open on Kalshi yet — both sides quoted high (no two-sided price). Shown so you can find it; check back closer to match time.</div>`
      : lean;
    const startTag = (!m.live && m.start && fmtStartTime(m.start))
      ? `<span class="starttime" title="scheduled start (Mountain Time)">🕒 ${fmtStartTime(m.start)}</span> ` : "";
    return `<div class="tn-match${up ? " upsetcard" : ""}${unopened ? " tn-unopened" : ""}">
        <div class="tn-mhead">${m.tour} · ${surfTag(m)} · Bo${m.best_of} ${tnDayTag(m)} ${startTag}${liveChip}
          <span class="tn-tier" title="${tText} — how much charted history backs this read">${tEmoji} ${tText}</span></div>
        ${kalshiWhere(m)}
        ${liveWin}
        ${upBanner}
        ${dipBanner}
        ${_tnPlayer(a)}${_tnPlayer(b)}
        ${leanBlock}
        <div class="tn-derived">${games}${distance}${aces}${setsLine ? `<span class="tn-chip">${setsLine}</span>` : ""}</div>
        ${insights ? `<div class="tn-insights">${insights}</div>` : ""}
      </div>`;
  }).join("");
}

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

// ---------------------------------------------------------------------------
// "What happened" — day-over-day history of the nightly deep run.
//
// Every stored day describes the move from the PREVIOUS run to itself, so the
// day you are looking at always answers "what changed to get here", including
// today. The pp figures next to each line are measured, not inferred: each one
// is a paired counterfactual run with that single player reverted (see
// deep_history.attribute), which is why some lines have a number and others say
// the change had no measurable effect.
let _histDate = null;
let _histFrom = null;                   // set = range mode, one combined box

async function loadDeepHistory(date, from) {
  const box = $("deepHistory");
  if (!box) return;
  _histFrom = from || null;
  box.innerHTML = `<div class="histwrap"><div class="small" style="color:var(--muted)">Loading what changed…</div></div>`;
  try {
    // A range asks the server to merge every run in the window into ONE answer.
    // Reading a week as seven boxes is how +3 on Monday and -4 on Thursday get
    // read as two moves rather than as a -1 week.
    const q = from
      ? `?from=${encodeURIComponent(from)}&to=${encodeURIComponent(date || "")}`
      : (date ? `?date=${encodeURIComponent(date)}` : "");
    renderDeepHistory(await (await fetch("/api/baseball/futures/deep/history" + q)).json());
  } catch (e) {
    box.innerHTML = `<div class="histwrap"><div class="small" style="color:var(--muted)">Couldn't load run history.</div></div>`;
  }
}

// "last N days" shortcut: the window ends at the newest run and reaches back N.
window.histSpan = (days) => {
  const ds = _histDates || [];
  if (!ds.length) return;
  const to = ds[0];
  const from = ds[Math.min(ds.length - 1, Math.max(0, days - 1))];
  loadDeepHistory(to, days <= 1 ? null : from);
};
let _histDates = [];

function _histLine(s) {
  // Colour only the measured effect, so the sentence itself stays readable.
  // Matches "+4.1pp" and "+4.1 ± 0.5pp" — the error bar is part of the figure.
  const m = String(s).match(/^(.*?)\s\s([+-][\d.]+(?:\s±\s[\d.]+)?pp)$/);
  if (!m) return `<li>${escapeHtml(s)}</li>`;
  const up = m[2].startsWith("+");
  return `<li>${escapeHtml(m[1])} <span class="histpp ${up ? "up" : "down"}">${escapeHtml(m[2])}</span></li>`;
}

function renderDeepHistory(d) {
  const box = $("deepHistory");
  if (!box) return;
  if (!d || d.empty || d.error) {
    box.innerHTML = `<div class="histwrap"><div class="histhead">📅 What happened</div>
      <div class="small" style="color:var(--muted)">${escapeHtml((d && (d.message || d.error)) || "No run history yet.")}</div></div>`;
    return;
  }
  _histDate = d.date;
  const dates = d.dates || [];
  _histDates = dates;
  const i = dates.indexOf(d.date);
  const newer = i > 0 ? dates[i - 1] : null;          // dates are newest-first
  const older = i >= 0 && i < dates.length - 1 ? dates[i + 1] : null;
  const oldest = dates[dates.length - 1] || d.date;
  const rng = !!d.range;
  const spanBtn = (n, lab) =>
    `<button class="track-mini${(rng ? d.days === n : n === 1) ? " primary-mini" : ""}"`
    + ` onclick="histSpan(${n})">${lab}</button>`;
  // From/to, with the single-day case being simply from = the previous run.
  const nav = `
    <div class="histnav">
      <button class="track-mini" ${older ? "" : "disabled"} onclick="loadDeepHistory('${older || ""}')">‹ prev</button>
      <input type="date" value="${escapeHtml(rng ? (d.from || oldest) : (d.prev_date || d.date))}"
             min="${escapeHtml(oldest)}" max="${escapeHtml(dates[0] || d.date)}"
             title="from"
             onchange="loadDeepHistory('${escapeHtml(d.to || d.date)}', this.value)"/>
      <span class="small" style="color:var(--muted)">→</span>
      <input type="date" value="${escapeHtml(d.to || d.date)}"
             min="${escapeHtml(oldest)}" max="${escapeHtml(dates[0] || d.date)}"
             title="to"
             onchange="loadDeepHistory(this.value, ${rng ? `'${escapeHtml(d.from || oldest)}'` : "null"})"/>
      <button class="track-mini" ${newer ? "" : "disabled"} onclick="loadDeepHistory('${newer || ""}')">next ›</button>
    </div>
    <div class="histnav" style="margin-top:4px">
      ${spanBtn(1, "1 day")}${spanBtn(3, "3 days")}${spanBtn(7, "1 week")}${spanBtn(14, "2 weeks")}
      ${rng ? `<span class="small" style="color:var(--muted)">combined over <b>${d.days}</b> runs, ${escapeHtml(d.from || "")} → ${escapeHtml(d.to || "")}</span>` : ""}
    </div>`;

  const teams = (d.teams || []).filter((t) => (t.what || []).length);
  const body = teams.length
    ? teams.map((t) => {
        const mv = t.move || 0;
        const cls = mv > 0 ? "up" : mv < 0 ? "down" : "flat";
        const arrow = (t.ws_prev != null)
          ? `<span class="small" style="color:var(--muted)">WS ${t.ws_prev}% → ${t.ws}%</span>` : "";
        return `<div class="histteam">
          <div class="histteam-hd"><b>${escapeHtml(t.name)}</b>
            <span class="histmove ${cls}">${mv > 0 ? "+" : ""}${mv.toFixed(1)}pp</span>
            ${arrow}</div>
          <ul class="histwhat">${(t.what || []).map(_histLine).join("")}</ul>
        </div>`;
      }).join("")
    : `<div class="small" style="color:var(--muted)">Nothing moved between these two runs — no roster changes and no games played.</div>`;

  const a = d.attribution || {};
  const note = a.priced
    ? `${a.priced} change${a.priced === 1 ? "" : "s"} priced by re-running the season with that one player reverted, on the same seeds — ${a.batches}×${(a.per_batch || 0).toLocaleString()} paired seasons each. The ± on a figure is measured from the spread across those runs, so it reflects that change rather than an assumed constant; anything not clearly separated from zero reads "no measurable effect" instead of a number.${a.skipped ? ` ${a.skipped} more change${a.skipped === 1 ? " is" : "s are"} listed without a figure.` : ""}`
    : `Changes are listed; measured pp figures appear once a run has priced them.`;

  box.innerHTML = `<div class="histwrap">
    <div class="histhead">📅 What happened
      <span class="small" style="color:var(--muted);font-weight:400">
        ${d.prev_date ? `changes from ${escapeHtml(d.prev_date)} → ${escapeHtml(d.date)}` : `first stored run (${escapeHtml(d.date)})`}</span>
    </div>
    ${nav}
    ${body}
    <div class="small" style="color:var(--muted);margin-top:8px">${escapeHtml(note)}</div>
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
     <div id="deepProg" style="margin-bottom:8px"></div>
     <div id="deepHistory" style="margin-bottom:8px"></div>`;
  // Only the deep engine keeps a nightly history; the fast model isn't snapshotted.
  if (d.engine === "deep") loadDeepHistory(_histDate);
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
    // Regular DraftKings lineups vs preseason. Defaults to the calendar so it is
    // right in August without being told, and stays a manual override the rest of
    // the year (a September user testing a preseason slate is a real thing).
    const preLbl = $("dfsNflPreLbl");
    if (preLbl) {
      preLbl.classList.toggle("hidden", !isNfl);
      const cb = $("dfsNflPre");
      if (cb && !cb.dataset.wired) { cb.dataset.wired = "1"; cb.checked = nflPreseason; }
    }
    // Showdown vs Classic. Left on auto by default because the CSV already says
    // which it is — a showdown export lists every player twice, once as CPT.
    if ($("dfsNflMode")) $("dfsNflMode").classList.toggle("hidden", !isNfl);
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
  // A silently-disabled availability filter is worse than none: it puts an IR
  // player in a lineup with nothing on screen to say it could not check.
  const stWarn = d.status_warning
    ? `<div class="small" style="margin-top:6px;color:#e0566a">⚠️ ${escapeHtml(d.status_warning)}</div>` : "";
  const flatNote = d.flat_note
    ? `<div class="small" style="margin-top:6px;color:var(--muted)">${escapeHtml(d.flat_note)}</div>` : "";
  return `<div class="combo hl prop">
    <div class="chead"><span class="ctag">🏈 NFL ${d.mode === "showdown" ? "showdown" : "lineup"} — ${objName}</span>
      <span class="small">Week ${d.week}${d.mode === "showdown" ? " · CPT + 5 FLEX" : ` · ${d.stack ? "QB stack" : "no stack"}`}</span></div>
    ${stWarn}
    <div class="cnums" style="margin:4px 0">
      <span>salary <b>$${nf(d.salary)}</b>/$${nf(d.cap)}</span>
      <span>proj <b>${d.proj}</b></span>
      <span>floor ${d.floor}</span><span>median ${d.median}</span>
      <span>ceiling <b class="ev pos">${d.ceiling}</b></span>
    </div>
    ${csHead}
    <div class="nfl-dfslist">${rows}</div>
    ${un}
    ${flatNote}
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
        preseason: !!(($("dfsNflPre") || {}).checked),
        grid: (($("dfsGrid") || {}).value || "").trim() || null,
        nfl_mode: ($("dfsNflMode") || {}).value || "auto",
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
let _cmbCatTypes = {};          // category -> [[type_value, chip_label], ...]
let _cmbCatLabels = {};         // category -> display label
const _cmbCounts = {};          // category -> per-sport leg count (string)
const _cmbTypeOff = new Set();  // type_values the user has turned OFF
async function loadCombineCats() {
  if (combineCatsLoaded) return;
  const meta = await (await fetch("/api/combine/meta")).json();
  // Back-compat: meta used to be a flat {key: label}; now {categories, types}.
  const cats = meta.categories || meta;
  _cmbCatTypes = meta.types || {};
  _cmbCatLabels = cats;
  // Default: nothing checked -- the user picks which sports to combine.
  $("combineCats").innerHTML = Object.entries(cats).map(([k, v]) =>
    `<label><input type="checkbox" value="${k}" onchange="onCmbCatToggle()"/> ${v}</label>`
  ).join("");
  combineCatsLoaded = true;
  onCmbCatToggle();
}
function onCmbCatToggle() { renderCmbTypeChips(); renderCmbCatCounts(); }
// Per-sport leg counts: for each checked sport, a small "how many legs" box.
// Blank = use the % floor (global mode); a number switches the maker to per-sport
// counts (0 = all that sport's legs, N = its N most likely). This is what lets
// "every baseball moneyline + 2 easy tennis" come out as asked instead of one
// floor picking whatever's highest.
function renderCmbCatCounts() {
  const el = $("cmbCatCounts");
  if (!el) return;
  const cats = _checkedCats();
  if (!cats.length) { el.innerHTML = ""; return; }
  const boxes = cats.map((c) => {
    const v = _cmbCounts[c] == null ? "" : _cmbCounts[c];
    return `<label class="cmbcount">${_cmbCatLabels[c] || c}
      <input type="number" min="0" max="40" value="${v}" placeholder="—"
        title="legs from this sport (blank = use the % floor, 0 = all, N = top N)"
        oninput="setCmbCount('${c}', this.value)"/></label>`;
  }).join(" ");
  el.innerHTML = `<div style="margin-top:4px">Legs per sport <span style="color:var(--muted)">(blank = use % floor · 0 = all · N = top N)</span>: ${boxes}</div>`;
}
window.setCmbCount = (cat, val) => {
  if (val === "" || val == null) delete _cmbCounts[cat];
  else _cmbCounts[cat] = String(Math.max(0, Math.min(40, parseInt(val, 10) || 0)));
};
// &per_cat= param: "mlb:0,tennis:2" from the filled-in count boxes of checked
// sports. Empty when none are filled (the maker stays in % floor mode).
function cmbPerCatParam() {
  const cats = new Set(_checkedCats());
  const parts = Object.entries(_cmbCounts)
    .filter(([c, v]) => cats.has(c) && v !== "" && v != null)
    .map(([c, v]) => `${c}:${v}`);
  return parts.length ? "&per_cat=" + encodeURIComponent(parts.join(",")) : "";
}
function _checkedCats() {
  return [...document.querySelectorAll("#combineCats input:checked")].map((i) => i.value);
}
// The type chips are the UNION of the checked sports' leg types. Deselecting a
// sport removes its exclusive types; a chip turned off excludes that leg type
// from the build. Shared types (e.g. Moneyline) persist while any sport that
// offers them stays checked.
function _cmbVisibleTypes() {
  const seen = new Map();       // type_value -> label (first sport wins)
  _checkedCats().forEach((c) => (_cmbCatTypes[c] || []).forEach(([tv, lbl]) => {
    if (!seen.has(tv)) seen.set(tv, lbl);
  }));
  return seen;
}
function renderCmbTypeChips() {
  const el = $("cmbTypeChips");
  if (!el) return;
  const seen = _cmbVisibleTypes();
  // Forget off-toggles for types no longer visible, so re-checking a sport
  // brings its types back ON.
  [..._cmbTypeOff].forEach((tv) => { if (!seen.has(tv)) _cmbTypeOff.delete(tv); });
  if (!seen.size) { el.innerHTML = ""; return; }
  const chips = [...seen].map(([tv, lbl]) =>
    `<span class="ptchip${_cmbTypeOff.has(tv) ? "" : " on"}" onclick="toggleCmbType(this,'${tv.replace(/'/g, "\\'")}')">${lbl}</span>`).join("");
  el.innerHTML = `Leg types <span style="color:var(--muted)">(click to exclude)</span>: <span class="ptchips">${chips}</span>`;
}
window.toggleCmbType = (el, tv) => {
  if (_cmbTypeOff.has(tv)) { _cmbTypeOff.delete(tv); el.classList.add("on"); }
  else { _cmbTypeOff.add(tv); el.classList.remove("on"); }
};
// &types= param: the ON subset of the visible types. Omitted when everything is
// on (= no filter); sent empty when everything is off (= no legs).
function cmbTypesParam() {
  const seen = _cmbVisibleTypes();
  if (!seen.size) return "";
  const on = [...seen.keys()].filter((tv) => !_cmbTypeOff.has(tv));
  if (on.length === seen.size) return "";                 // all on -> no filter
  return "&types=" + on.map(encodeURIComponent).join(",");
}
async function buildRecommended() {
  const cats = [...document.querySelectorAll("#combineCats input:checked")].map((i) => i.value);
  const out = $("cmbRecOut");
  if (!cats.length) { out.innerHTML = `<div class="empty">Check one or more sports above first.</div>`; return; }
  const date = ($("bbDate") && $("bbDate").value) || new Date().toISOString().slice(0, 10);
  out.innerHTML = `<div class="empty">Building the best combos across ${cats.length} sport${cats.length > 1 ? "s" : ""}…</div>`;
  try {
    const d = await (await fetch(`/api/combine/recommended?cats=${cats.join(",")}&date=${date}${cmbTypesParam()}${cmbLiveParam()}`)).json();
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
// Mega maker's live opt-in. Same rule as the baseball tab: off unless ticked,
// because a live board is priced from a snapshot seconds old.
function cmbLiveParam() {
  const el = $("cmbLive");
  return (el && el.checked) ? "&live=1" : "";
}

window.renderCmbLiveWarn = () => {
  const el = $("cmbLiveWarn");
  if (!el) return;
  const on = $("cmbLive") && $("cmbLive").checked;
  el.innerHTML = on ? `<div class="livewarn">
    <b>\u26a0\ufe0f Live pricing is on</b> — games already under way are simulated forward from the
    current score, count and base-out state, with what each player has banked counted toward his line.
    Prices come from the live Kalshi market.
    <div style="margin-top:4px">This is a <b>snapshot</b>: one pitch can move it. Re-build right before you place.</div>
    <div style="margin-top:4px">In a <b>multi-sport parlay</b>, a live leg can settle long before the rest — the slip is only decided when every event finishes.</div>
  </div>` : "";
};

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
      + `&legs_mode=${legsMode}&payout_mode=${payoutMode}&conn=${conn}`
      + cmbTypesParam() + cmbPerCatParam() + cmbLiveParam();
    const d = await (await fetch(`/api/combine?${q}`)).json();
    if (d.error) { out.innerHTML = `<div class="empty">${d.error}</div>`; return; }
    let html = "";
    if (d.counts && Object.keys(d.counts).length)
      html += `<div class="small" style="margin-bottom:8px">Legs available: ${Object.entries(d.counts).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>`;
    if (d.combo) {
      const c = d.combo;
      const wantPayout = payoutMode !== "off" && p > 1;
      let title, note = "";
      if (c.per_cat) {
        // Per-sport budget mode: leg counts came from the per-sport boxes, not
        // one % floor, so describe it that way.
        const used = c.per_cat_used || {};
        const breakdown = Object.entries(used).map(([k, v]) =>
          `${(_cmbCatLabels[k] || k)} ${v}`).join(" + ");
        title = `🎰 ${c.legs_used}-leg parlay → ${c.fair_payout_x}× (${breakdown})`;
        if (c.capped)
          note += `<div class="small">⚠️ Capped at ${c.legs_used} legs — the highest-probability legs were kept.</div>`;
        if (c.target_payout_x && c.payout_reached === false)
          note += `<div class="small">⚠️ Couldn't reach ${c.target_payout_x}× — with these sports/counts and the ${t}% floor the most is <b>${c.fair_payout_x}×</b>. Raise a count, lower the floor, or add a sport.</div>`;
        else if (c.target_payout_x)
          note += `<div class="small">🎯 Reached your ${c.target_payout_x}× target (steered the free-to-choose sports toward it while keeping the safest legs that get there).</div>`;
        note += `<div class="small">Built from your per-sport counts (${breakdown}). At ${c.fair_payout_x}× the chance is ~<b>${c.combined_prob_pct}%</b> (≈1 in ${Math.round(c.fair_payout_x)}).</div>`;
      } else {
        title = `🎰 ${c.legs_used || c.n_legs}-leg mega parlay → ${c.fair_payout_x}× (every leg ≥ ${t}%)`;
        if (c.expanded && c.legs_used !== c.requested_legs)
          note += `<div class="small">Used <b>${c.legs_used}</b> legs (you set ${c.requested_legs}) to best fit your targets while keeping every leg ≥ ${t}%.</div>`;
        if (c.legs_met === false && legsMode === "require")
          note += `<div class="small">⚠️ Couldn't field exactly ${c.legs_target} legs ≥ ${t}% — showing the closest (${c.legs_used}).</div>`;
        if (wantPayout && c.payout_reached === false)
          note += `<div class="small">⚠️ Couldn't reach ${p}× with every leg ≥ ${t}% — the best at that floor is <b>${c.fair_payout_x}×</b>. Lower the floor, drop the leg-count requirement, or add categories.</div>`;
        if (c.hard_ok === false)
          note += `<div class="small" style="color:#e0566a">⚠️ Your required target(s) couldn't both be met${conn === "and" ? " (AND)" : ""} — showing the closest parlay. Try switching AND→OR or relaxing one target to <b>recommend</b>.</div>`;
        note += `<div class="small">At ${c.fair_payout_x}× the chance is ~<b>${c.combined_prob_pct}%</b> (≈1 in ${Math.round(c.fair_payout_x)}). ${c.legs_meeting_target}/${c.legs_used} legs meet the ${t}% target.</div>`;
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
    // Blend components graded separately: does the deep engine earn its weight?
    const ms = r.model_split;
    if (ms && (ms.factor || ms.deep)) {
      const f = (x, nm) => x ? `${nm} <b>${x.acc_pct}%</b> <span style="color:var(--muted)">(Brier ${x.brier}, ${x.n})</span>` : `${nm} —`;
      extra.push(`🧬 Split: ${f(ms.factor, "factor")} · ${f(ms.deep, "deep")} · ${f(ms.blend, "blend")} — the blend weight auto-tunes on this once 40+ games carry both.`);
    }
    // Live calibration: temperature reining in high-end overconfidence, fit on
    // this record. T=1.00 = no correction yet (too little data); >1 = active.
    const ct = r.calibration_temps;
    if (ct && (ct.win_t > 1.03 || ct.prop_t > 1.03)) {
      const bits = [];
      if (ct.win_t > 1.03) bits.push(`win 80%→<b>${ct.win_ex80}%</b> <span style="color:var(--muted)">(${ct.win_n})</span>`);
      if (ct.prop_t > 1.03) bits.push(`props 80%→<b>${ct.prop_ex80}%</b> <span style="color:var(--muted)">(${ct.prop_n})</span>`);
      extra.push(`🎯 Calibrating overconfidence: ${bits.join(" · ")} — <b>only</b> high-confidence picks are pulled toward their real hit-rate; moderate favorites (≤~60%) are left as-is, so the correction never manufactures a fake negative edge (auto-fit on this record).`);
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

  // Best-ball team grader (multi-team)
  if ($("gradeTeams")) {
    loadGradeTeams();                          // restore saved teams (else a blank one)
    $("gradeAddBtn").addEventListener("click", () => addGradeTeam());
    $("gradeMineBtn").addEventListener("click", gradeAddMine);
    $("gradeSaveBtn").addEventListener("click", () => saveGradeTeams(false));
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
