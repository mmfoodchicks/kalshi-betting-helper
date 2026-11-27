// The Pick6 paper ledger (2026-11-27; pick6_ledger.py, the owner's backlog item 7). Under the NFL tab's
// Pick 6 view, for every sport: enter a DraftKings Pick6 entry by hand -- the picks, the entry type and the
// payout schedule DraftKings states on the entry, a paper stake -- then see each pick graded off the box
// score, Vigil's figure beside it, and the record. No inline handlers; every string escaped.
const _p6l = { sports: [], rules: [], pardon: {}, entries: [], record: null, draft: null, msg: "", msgBad: false, wired: false };

function _p6lEsc(x) {
  return String(x == null ? "" : x).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function _p6lMtToday() {
  // en-CA formats as YYYY-MM-DD; the owner's day is Mountain time.
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Denver" }).format(new Date());
}
function _p6lWhen(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString("en-US", { timeZone: "America/Denver", month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit" }) + " MT";
}
function _p6lBlank() { return { player: "", stat: "", stat_label: "", kind: "full", line: "", side: "More" }; }
function _p6lNewDraft(sport) {
  return { sport: sport || "nfl", game_date: _p6lMtToday(), entry_type: "", stake: "", schedule: "", notes: "",
    picks: [_p6lBlank(), _p6lBlank()] };
}
function _p6lSport(k) { return _p6l.sports.find((s) => s.key === k) || { key: k, label: k, stats: [], auto: false }; }
function _p6lBody() { return document.getElementById("p6LedgerBody"); }

// Called when the Pick 6 subtab opens.
function p6LedgerShow() {
  const box = _p6lBody();
  if (!box) return;
  if (!_p6l.wired) {
    _p6l.wired = true;
    box.addEventListener("click", _p6lOnClick);
    box.addEventListener("change", _p6lOnChange);
    box.addEventListener("input", _p6lOnInput);
  }
  _p6lLoad();
}

async function _p6lLoad() {
  try {
    const d = await (await fetch("/api/pick6/ledger")).json();
    if (d.error) { _p6l.msg = d.error; _p6l.msgBad = true; }
    else { _p6l.sports = d.sports || []; _p6l.rules = d.rules || []; _p6l.pardon = d.pardon || {}; _p6l.kinds = d.kinds || []; _p6l.entries = d.entries || []; _p6l.record = d.record; }
  } catch (e) { _p6l.msg = "The ledger is unavailable."; _p6l.msgBad = true; }
  if (!_p6l.draft) _p6l.draft = _p6lNewDraft();
  _p6lDraw();
}

async function _p6lSend(url, method, body) {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  let d = {};
  try { d = await r.json(); } catch (e) { d = { error: `HTTP ${r.status}` }; }
  if (!r.ok && !d.error) d.error = `HTTP ${r.status}`;
  return d;
}

function _p6lPct(p) { return p == null ? null : Math.round(p * 1000) / 10; }

function _p6lForm() {
  const dr = _p6l.draft, sp = _p6lSport(dr.sport);
  const sports = _p6l.sports.map((s) => `<option value="${_p6lEsc(s.key)}"${s.key === dr.sport ? " selected" : ""}>${_p6lEsc(s.label)}</option>`).join("");
  const picks = dr.picks.map((p, i) => {
    const custom = !sp.stats.length || p.stat === "custom";
    const opts = sp.stats.map((s) => `<option value="${_p6lEsc(s.key)}"${s.key === p.stat ? " selected" : ""}>${_p6lEsc(s.label)}</option>`).join("");
    const statSel = sp.stats.length
      ? `<select data-p6l-pick="${i}" data-p6l-f="stat"><option value="">stat…</option>${opts}<option value="custom"${p.stat === "custom" ? " selected" : ""}>Other stat (graded by hand)</option></select>`
      : "";
    return `<div class="p6l-pick">
      <span class="p6l-n">${i + 1}</span>
      <input type="text" maxlength="60" placeholder="player" value="${_p6lEsc(p.player)}" data-p6l-pick="${i}" data-p6l-f="player"/>
      ${statSel}
      ${custom ? `<input type="text" maxlength="40" placeholder="stat, as DraftKings names it" value="${_p6lEsc(p.stat_label)}" data-p6l-pick="${i}" data-p6l-f="stat_label"/>
        <select data-p6l-pick="${i}" data-p6l-f="kind" title="only a full-game stat can be pardoned">${(_p6l.kinds || []).map((k) => `<option value="${_p6lEsc(k.key)}"${k.key === (p.kind || "full") ? " selected" : ""}>${_p6lEsc(k.label)}</option>`).join("")}</select>` : ""}
      <input type="number" step="0.5" min="0" inputmode="decimal" placeholder="line" value="${_p6lEsc(p.line)}" data-p6l-pick="${i}" data-p6l-f="line" class="p6l-line"/>
      <select data-p6l-pick="${i}" data-p6l-f="side"><option${p.side === "More" ? " selected" : ""}>More</option><option${p.side === "Less" ? " selected" : ""}>Less</option></select>
      ${dr.picks.length > 2 ? `<button type="button" class="p6l-x" data-p6l-rm="${i}" title="remove this pick">×</button>` : ""}
    </div>`;
  }).join("");
  return `<div class="p6l-form">
    <div class="p6l-grid">
      <label>Sport<select data-p6l-f="sport">${sports}</select></label>
      <label>Game date<input type="date" value="${_p6lEsc(dr.game_date)}" data-p6l-f="game_date"/></label>
      <label>Entry type<input type="text" maxlength="40" placeholder="as DraftKings labels it" value="${_p6lEsc(dr.entry_type)}" data-p6l-f="entry_type"/></label>
      <label>Paper stake ($)<input type="number" min="0" step="1" inputmode="decimal" value="${_p6lEsc(dr.stake)}" data-p6l-f="stake"/></label>
    </div>
    <div class="p6l-picks">${picks}</div>
    ${dr.picks.length < 6 ? `<button type="button" data-p6l-add="1">+ Add pick</button>` : ""}
    <label class="p6l-sched">Payout schedule, as DraftKings shows it on the entry
      <input type="text" maxlength="300" placeholder="${dr.picks.length}/${dr.picks.length}=25, ${dr.picks.length - 1}/${dr.picks.length}=2" value="${_p6lEsc(dr.schedule)}" data-p6l-f="schedule"/></label>
    <div class="small p6l-help">k/n=x: k correct of n live picks returns x times the stake (stake included). ${sp.auto ? `${_p6lEsc(sp.label)} picks grade themselves off the box score; "Other stat" ones by hand.` : `${_p6lEsc(sp.label)} picks are graded by hand: type each final stat once the game is over.`}</div>
    <div class="p6l-actions"><button type="button" class="primary" data-p6l-save="1">Save entry</button></div>
  </div>`;
}

// The owner's answers to a pick in review (pick6_ledger.DECISIONS).
const _P6L_DECIDE = { met: "He played: grade it", not_met: "Did not meet: void", loss: "Loss (no pardon)", pardoned: "Pardoned" };

function _p6lResult(p) {
  const cls = { hit: "p6l-hit", miss: "p6l-miss", tie: "p6l-void", void: "p6l-void", pending: "p6l-pend", review: "p6l-tie" }[p.result] || "p6l-pend";
  // DraftKings voids a tie (its rules pages, confirmed by the owner 2026-11-27)
  const lbl = p.result === "tie" ? "tie (void)" : p.result === "review" ? `review: ${p.review || "check"}` : p.result;
  return `<span class="p6l-res ${cls}">${_p6lEsc(lbl)}</span>`;
}

function _p6lEntry(e) {
  const sp = _p6lSport(e.sport);
  const sched = (e.schedule || []).map((r) => `${r[0]}/${r[1]}=${r[2]}x`).join(", ");
  let status;
  if (e.status === "settled") {
    const pl = e.pnl || 0;
    status = `<span class="p6l-res ${pl > 0 ? "p6l-hit" : pl < 0 ? "p6l-miss" : "p6l-void"}">${e.n_hit}/${e.n_live} · ${e.payout_x}x · ${pl >= 0 ? "+" : "-"}$${Math.abs(pl).toFixed(2)}${e.credits ? " (credits)" : ""}</span>`;
  } else if (e.status === "awaiting_payout") {
    status = `<span class="p6l-res p6l-tie">needs DraftKings' payout</span>`;
  } else status = `<span class="p6l-res p6l-pend">open</span>`;
  const picks = (e.picks || []).map((p) => {
    const vig = p.p_model != null
      ? `<span title="${_p6lEsc(p.model_src)}">Vigil <b>${_p6lPct(p.p_model)}%</b></span>`
      : `<span class="p6l-muted" title="${_p6lEsc(p.model_src)}">no model figure</span>`;
    const by = p.graded_by ? ` <span class="p6l-muted">(${_p6lEsc(p.graded_by === "owner" ? "by hand" : p.graded_by)})</span>` : "";
    return `<div class="p6l-prow">
      <div class="p6l-pline"><span class="p6side ${p.side === "More" ? "more" : "less"}">${_p6lEsc(p.side)} ${_p6lEsc(p.line)}</span>
        <b>${_p6lEsc(p.player)}</b> <span class="p6l-muted">${_p6lEsc(p.stat_label)}</span></div>
      <div class="p6l-pline">${vig} · ${_p6lResult(p)}${p.actual != null ? ` actual <b>${_p6lEsc(p.actual)}</b>` : ""}${by}</div>
      ${p.note ? `<div class="small p6l-muted">${_p6lEsc(p.note)}</div>` : ""}
      ${p.evidence ? `<div class="small p6l-muted">evidence: ${_p6lEsc(p.evidence)}</div>` : ""}
      ${(p.decisions || []).length ? `<div class="p6l-own">${p.decisions.map((k) => `<button type="button" class="${k === "met" || k === "loss" ? "" : "primary"}" data-p6l-decide="${p.id}" data-p6l-d="${_p6lEsc(k)}">${_p6lEsc(_P6L_DECIDE[k] || k)}</button>`).join("")}</div>` : ""}
      <div class="p6l-own"><input type="number" step="any" inputmode="decimal" placeholder="final stat" data-p6l-actual="${p.id}"/>
        <button type="button" data-p6l-set="${p.id}">Set</button><button type="button" data-p6l-void="${p.id}">Void</button>
        ${p.result !== "pending" ? `<button type="button" data-p6l-reset="${p.id}">Re-grade</button>` : ""}</div>
    </div>`;
  }).join("");
  const awaiting = e.status === "awaiting_payout"
    ? `<div class="p6l-await small">${_p6lEsc(e.why)}<div class="p6l-own"><input type="text" maxlength="100" placeholder="${e.n_hit}/${e.n_live}=x" data-p6l-rows="${e.id}"/><button type="button" data-p6l-addrows="${e.id}">Add</button></div></div>`
    : "";
  return `<div class="p6l-entry">
    <div class="p6l-ehead"><b>${_p6lEsc(sp.label)}</b> · ${_p6lEsc(e.game_date)}${e.entry_type ? ` · ${_p6lEsc(e.entry_type)}` : ""} · $${Number(e.stake).toFixed(2)} paper · ${status}
      <button type="button" class="danger p6l-del" data-p6l-del="${e.id}" title="delete this entry">Delete</button></div>
    <div class="small p6l-muted">entered ${_p6lEsc(_p6lWhen(e.created_at))}${e.historical ? " · after its game date (no Vigil figures)" : ""} · schedule ${_p6lEsc(sched)}</div>
    ${awaiting}
    <div class="p6l-plist">${picks}</div>
  </div>`;
}

function _p6lFlag(f) { return f ? ` <span class="p6l-flag">${_p6lEsc(f)}</span>` : ""; }

function _p6lRecord() {
  const r = _p6l.record;
  if (!r) return "";
  const hits = (r.hit_rate || []).map((h) => `<tr><td>${_p6lEsc(_p6lSport(h.sport).label)} ${_p6lEsc(h.stat)} ${_p6lEsc(h.side)}</td>
    <td>${h.hits}/${h.n}</td><td>${h.hit_pct == null ? "-" : `${h.hit_pct}% ±${h.ci95}`}${_p6lFlag(h.flag)}</td><td>${h.ties}/${h.voids}</td></tr>`).join("");
  const cal = (rows) => rows.filter((c) => c.n).map((c) => `<tr><td>${_p6lEsc(c.sport === "all" ? "All sports" : _p6lSport(c.sport).label)}</td>
    <td>${c.n}${_p6lFlag(c.flag)}</td><td>${c.vigil_pct}% vs ${c.hit_pct}%</td>
    <td>${c.log_loss}${c.log_loss_se != null ? ` ±${c.log_loss_se}` : ""} <span class="p6l-muted">(coin ${c.coin_log_loss})</span></td>
    <td>${c.brier} <span class="p6l-muted">(coin ${c.coin_brier})</span></td></tr>`).join("");
  const calRows = cal([...(r.calibration || []), r.calibration_all || { n: 0 }]);
  const bins = (r.calibration_bins || []).map((b) => `<tr><td>${_p6lEsc(b.bin)}</td><td>${b.n}${_p6lFlag(b.flag)}</td><td>${b.vigil_pct}%</td><td>${b.hit_pct}%</td></tr>`).join("");
  const roi = [...(r.roi || []), r.roi_all || { n: 0 }].filter((x) => x.n).map((x) => `<tr><td>${_p6lEsc(x.sport === "all" ? "All sports" : _p6lSport(x.sport).label)}</td>
    <td>${x.n}${_p6lFlag(x.flag)}</td><td>$${x.staked.toFixed(2)}</td><td>$${x.returned.toFixed(2)}</td>
    <td class="${x.pnl > 0 ? "ev pos" : x.pnl < 0 ? "ev neg" : ""}">${x.pnl >= 0 ? "+" : "-"}$${Math.abs(x.pnl).toFixed(2)}</td>
    <td>${x.roi_pct == null ? "-" : `${x.roi_pct}%`}</td><td>${x.cashed}${x.credit_paid ? ` (${x.credit_paid} in credits)` : ""}</td></tr>`).join("");
  const st = r.status || {};
  const rv = r.in_review || {};
  const rev = Object.keys(rv).map((k) => `${rv[k]} ${k}`).join(", ");
  const waiting = ["open", "awaiting_payout"].filter((k) => st[k]).map((k) => `${st[k].n} ${k === "open" ? "open" : "awaiting DraftKings' payout"}`).join(" · ");
  return `<h3 class="p6l-h">Record</h3>
    ${waiting ? `<div class="small p6l-muted">${_p6lEsc(waiting)} (not in the ROI yet)</div>` : ""}
    ${hits ? `<div class="p6l-scroll"><table class="p6l-table"><tr><th>Pick type</th><th>Hit</th><th>Rate (95%)</th><th>Ties/voids</th></tr>${hits}</table></div>` : `<div class="empty">No graded picks yet.</div>`}
    <div class="small p6l-muted">A tie is void (DraftKings' rule) and, like any void, counts nowhere; a pick still in review counts nowhere until you decide it${rev ? ` (${_p6lEsc(rev)} waiting)` : ""}.</div>
    <h3 class="p6l-h">Vigil's calibration (picks it priced)</h3>
    ${calRows ? `<div class="p6l-scroll"><table class="p6l-table"><tr><th>Sport</th><th>Picks</th><th>Vigil vs hit</th><th>Log loss</th><th>Brier</th></tr>${calRows}</table></div>
      ${bins ? `<div class="p6l-scroll"><table class="p6l-table"><tr><th>Vigil said</th><th>Picks</th><th>Mean</th><th>Hit</th></tr>${bins}</table></div>` : ""}` : `<div class="empty">No graded pick Vigil priced yet.</div>`}
    ${_p6lResearch(r)}
    <h3 class="p6l-h">Paper ROI (settled entries)</h3>
    ${roi ? `<div class="p6l-scroll"><table class="p6l-table"><tr><th>Sport</th><th>Entries</th><th>Staked</th><th>Returned</th><th>P/L</th><th>ROI</th><th>Cashed</th></tr>${roi}</table></div>` : `<div class="empty">No settled entries yet.</div>`}`;
}

// The owner's research split (pick6_ledger._research_row): outright accuracy counts a void as not a hit;
// the conditional one leaves voids out; void frequency by cause. Each figure with its n and 95% interval.
function _p6lCI(x) {
  if (!x || x.pct == null) return "-";
  return `${x.pct}% <span class="p6l-muted">${x.lo}-${x.hi} · ${x.k}/${x.n}</span>`;
}
function _p6lResearch(r) {
  const rows = (r.research || []).map((x) => `<tr><td>${_p6lEsc(x.sport === "all" ? "All sports" : _p6lSport(x.sport).label)} ${_p6lEsc(x.side)}${_p6lFlag(x.flag)}</td>
    <td>${_p6lCI(x.outright)}</td><td>${_p6lCI(x.conditional)}</td><td>${_p6lCI(x.void)}</td>
    <td>${["dnp", "tie", "pardon", "other"].map((c) => `${_P6L_CAUSE[c]} ${x.void_by_cause[c].k}`).join(" · ")}</td></tr>`).join("");
  if (!rows) return "";
  return `<h3 class="p6l-h">Prediction accuracy and voids (research)</h3>
    <div class="p6l-scroll"><table class="p6l-table"><tr><th>Sport, side</th><th>Outright</th><th>Non-void</th><th>Void rate</th><th>Voids by cause</th></tr>${rows}</table></div>
    <div class="small p6l-muted">Outright counts every decided pick and a void as NOT a hit; non-void leaves voids (ties included) out. Intervals are 95% Wilson; nothing in review is counted.</div>`;
}
const _P6L_CAUSE = { dnp: "did not play", tie: "tie", pardon: "pardon", other: "other" };

function _p6lRules() {
  const rows = _p6l.rules.map((r) => `<li><span class="p6l-res ${r.verified ? "p6l-hit" : "p6l-tie"}">${_p6lEsc(r.basis || (r.verified ? "stated" : "unverified"))}</span> ${_p6lEsc(r.text)} <span class="p6l-muted">Source: ${_p6lEsc(r.source)}.</span></li>`).join("");
  const pk = Object.keys(_p6l.pardon || {});
  const pardon = pk.length
    ? `The Pardon by sport: ${pk.map((k) => `<b>${_p6lEsc(k.toUpperCase())}</b> ${_p6lEsc(_p6l.pardon[k])}`).join("; ")}.`
    : "";
  return `<details class="p6l-rules"><summary>How picks are graded (DraftKings' rules, and what could not be verified)</summary><ul>${rows}</ul><div class="small p6l-muted">${pardon}</div></details>`;
}

function _p6lDraw() {
  const box = _p6lBody();
  if (!box) return;
  const list = _p6l.entries.length ? _p6l.entries.map(_p6lEntry).join("") : `<div class="empty">No entries yet.</div>`;
  box.innerHTML = `${_p6lForm()}
    <div class="small p6l-msg${_p6l.msgBad ? " ev neg" : ""}">${_p6lEsc(_p6l.msg)}</div>
    ${_p6lRules()}
    <div class="row-between p6l-listhead"><h3 class="p6l-h">Entries</h3><button type="button" data-p6l-grade="1">Grade now</button></div>
    ${list}
    ${_p6lRecord()}`;
}

function _p6lSay(msg, bad) { _p6l.msg = msg || ""; _p6l.msgBad = !!bad; }

function _p6lOnInput(ev) {
  // Text and number fields update the draft without a redraw, so the field keeps its focus.
  const t = ev.target;
  if (!t || !t.dataset || !t.dataset.p6lF || !_p6l.draft) return;
  if (t.dataset.p6lPick != null) _p6l.draft.picks[Number(t.dataset.p6lPick)][t.dataset.p6lF] = t.value;
  else _p6l.draft[t.dataset.p6lF] = t.value;
}

function _p6lOnChange(ev) {
  const t = ev.target;
  if (!t || !t.dataset || !t.dataset.p6lF || !_p6l.draft) return;
  _p6lOnInput(ev);
  if (t.dataset.p6lF === "sport") {
    _p6l.draft.picks.forEach((p) => { p.stat = ""; p.stat_label = ""; });
    _p6lDraw();
  } else if (t.dataset.p6lF === "stat") _p6lDraw();
}

async function _p6lOnClick(ev) {
  const b = ev.target.closest ? ev.target.closest("button") : null;
  if (!b || !b.dataset) return;
  const ds = b.dataset, dr = _p6l.draft;
  let d = null;
  if (ds.p6lAdd && dr.picks.length < 6) { dr.picks.push(_p6lBlank()); _p6lDraw(); return; }
  if (ds.p6lRm != null && dr.picks.length > 2) { dr.picks.splice(Number(ds.p6lRm), 1); _p6lDraw(); return; }
  if (ds.p6lSave) {
    const sp = _p6lSport(dr.sport);
    const body = { sport: dr.sport, game_date: dr.game_date, entry_type: dr.entry_type, stake: dr.stake, schedule: dr.schedule,
      picks: dr.picks.map((p) => ({ player: p.player, stat: sp.stats.length ? p.stat : "custom", stat_label: p.stat_label, kind: p.kind || "full", line: p.line, side: p.side })) };
    d = await _p6lSend("/api/pick6/ledger", "POST", body);
    if (!d.error) { _p6l.draft = _p6lNewDraft(dr.sport); _p6lSay("Saved. Vigil's figures are on the entry below."); }
  } else if (ds.p6lSet) {
    const inp = _p6lBody().querySelector(`input[data-p6l-actual="${CSS.escape(ds.p6lSet)}"]`);
    d = await _p6lSend(`/api/pick6/ledger/pick/${encodeURIComponent(ds.p6lSet)}`, "POST", { actual: inp ? inp.value : "" });
  } else if (ds.p6lVoid) {
    d = await _p6lSend(`/api/pick6/ledger/pick/${encodeURIComponent(ds.p6lVoid)}`, "POST", { void: true });
  } else if (ds.p6lDecide) {
    d = await _p6lSend(`/api/pick6/ledger/pick/${encodeURIComponent(ds.p6lDecide)}`, "POST", { decision: ds.p6lD });
  } else if (ds.p6lReset) {
    d = await _p6lSend(`/api/pick6/ledger/pick/${encodeURIComponent(ds.p6lReset)}`, "POST", { reset: true });
  } else if (ds.p6lAddrows) {
    const inp = _p6lBody().querySelector(`input[data-p6l-rows="${CSS.escape(ds.p6lAddrows)}"]`);
    d = await _p6lSend(`/api/pick6/ledger/${encodeURIComponent(ds.p6lAddrows)}/schedule`, "POST", { text: inp ? inp.value : "" });
  } else if (ds.p6lDel) {
    if (!window.confirm("Delete this paper entry and its picks?")) return;
    d = await _p6lSend(`/api/pick6/ledger/${encodeURIComponent(ds.p6lDel)}`, "DELETE");
  } else if (ds.p6lGrade) {
    _p6lSay("Grading…"); _p6lDraw();
    d = await _p6lSend("/api/pick6/ledger/grade", "POST", {});
    if (!d.error) _p6lSay(d.busy ? "A grading pass is already running." : `Graded ${d.graded} pick${d.graded === 1 ? "" : "s"} (a box that is not final yet waits).`);
  } else return;
  if (d && d.error) { _p6lSay(d.error, true); _p6lDraw(); return; }
  if (!ds.p6lSave && !ds.p6lGrade) _p6lSay("");
  _p6lLoad();
}
