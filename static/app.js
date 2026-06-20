"use strict";

const $ = (id) => document.getElementById(id);

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
      <div class="small">${p.detail}</div>
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
    action = `<div class="actionline">
        ${badge(sig.recommendation, sig.strength)}
        <span class="edgeval pos">+${bestEdge}¢ edge</span>
      </div>
      <div class="plain">Buy <b>${side}</b> at <b>${cost}¢</b> → model fair value <b>${fair}¢</b>${sig.dip_note ? ` · 💡 ${sig.dip_note}` : ""}</div>`;
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

  return `<div class="${rowCls}">
    <div class="scanhead">
      <div class="strike">${m.subtitle || m.ticker}</div>
      <div class="small">closes ${fmtCountdown(secs)} · Kalshi YES ${m.yes_ask ?? "–"}¢ / NO ${m.no_ask ?? "–"}¢</div>
    </div>
    ${action}
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
    box.innerHTML = d.markets.map(renderScanRow).join("");
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
  const market = g.pick_price_cents != null
    ? `Kalshi ${g.pick_price_cents}¢ · <b class="${edge >= 0 ? "ev pos" : "ev neg"}">${edge >= 0 ? "+" : ""}${edge}¢ edge</b>`
    : `<span style="color:var(--muted)">no Kalshi price matched</span>`;
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
  return `<div class="${cls}">
    <div class="top">
      <div>
        <div class="matchup">${g.matchup}</div>
        <div class="pick">Pick: ${g.pick} &nbsp;(${g.pick_pct}%)</div>
      </div>
      <div class="small" style="text-align:right">conf ${g.confidence}%<br>${g.status}</div>
    </div>
    <div class="winbar"><div class="fill" style="width:${pct}%"></div>
      <div class="lbl">${g.away_name.split(" ").pop()} ${Math.round(g.p_away*100)}% — ${Math.round(g.p_home*100)}% ${g.home_name.split(" ").pop()}</div>
    </div>
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
  </div>`;
}

function renderCombo(c, tag, extraCls) {
  const legs = c.legs.map((l) =>
    `<li>${l.pick} <span style="color:var(--muted)">(${l.prob_pct}%${l.price_cents != null ? `, ${l.price_cents}¢` : ""})</span></li>`
  ).join("");
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

async function loadBaseball() {
  const gamesBox = $("bbGames");
  const combosBox = $("bbCombos");
  const date = $("bbDate").value;
  gamesBox.innerHTML = `<div class="empty">Loading slate…</div>`;
  combosBox.innerHTML = `<div class="empty">Crunching combos…</div>`;
  try {
    const d = await (await fetch("/api/baseball/today?date=" + date)).json();
    if (d.error) { gamesBox.innerHTML = `<div class="empty">${d.error}</div>`; combosBox.innerHTML = ""; return; }
    if (!d.games.length) {
      gamesBox.innerHTML = `<div class="empty">No MLB games scheduled for ${date}.</div>`;
      combosBox.innerHTML = `<div class="empty">No games, no combos.</div>`;
      return;
    }
    gamesBox.innerHTML = d.games.map(renderGame).join("");

    const c = d.combos;
    let html = "";
    if (c.safest) html += renderCombo(c.safest, "🛡️ Safest combo", "hl");
    if (c.best_value && JSON.stringify(c.best_value.legs) !== JSON.stringify(c.safest && c.safest.legs))
      html += renderCombo(c.best_value, "💰 Best value (+EV)", "hl value");
    html += `<div class="small" style="margin:6px 0">More combos by combined chance:</div>`;
    html += c.all.map((x) => renderCombo(x)).join("");
    combosBox.innerHTML = html;
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
      if (tab === "baseball" && !$("bbGames").dataset.loaded) {
        $("bbGames").dataset.loaded = "1";
        loadBaseball();
      }
    });
  });
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

  // Baseball setup
  setupTabs();
  $("bbDate").value = new Date().toISOString().slice(0, 10);
  $("bbBtn").addEventListener("click", loadBaseball);

  refreshMarkets();
  setInterval(refreshMarkets, 5000);   // live updates for tracked markets
  setInterval(refreshPreview, 5000);   // keep the preview fresh too
  setInterval(() => { if (lastScan.coin) runScan(); }, 8000); // refresh scanner
}

init();
