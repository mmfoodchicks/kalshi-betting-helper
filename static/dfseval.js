// The custom lineup evaluator (2026-11-26; dfs_evaluate.py, research/reports/
// dfs_cache_evaluator.md). Under every tournament board: build a lineup slot by
// slot from the board's own pool (or start from one of Vigil's and swap), and
// the PC scores it -- with Vigil's top lineups beside it -- on the slate's FINAL
// worlds: simulated on their own seed, so nothing on the board was chosen on
// them. No inline handlers; every server string escaped.
const _dxe = { board: null, sel: {}, reqId: null, timer: null, contest: 0, result: null };
const _DXE_CL = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"];
const _DXE_SD = ["CPT", "FLEX", "FLEX", "FLEX", "FLEX", "FLEX"];

function _dxeEsc(x) {
  return String(x == null ? "" : x).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function _dxeSlots(b) { return b && b.kind === "showdown" ? _DXE_SD : _DXE_CL; }
function _dxeElig(slot, p) {
  if (slot === "FLEX") return (_dxe.board || {}).kind === "showdown" ? true : ["RB", "WR", "TE"].includes(p.pos);
  if (slot === "CPT") return true;
  return p.pos === slot;
}
function _dxeSalary(p, slot) {
  return slot === "CPT" ? (p.cpt_salary || Math.round(1.5 * p.salary)) : p.salary;
}
function _dxeBox() {
  let box = document.getElementById("dfsEvalBox");
  const host = document.getElementById("dfsTourney");
  if (!host) return null;
  if (!box) {
    box = document.createElement("div");
    box.id = "dfsEvalBox";
    box.className = "card dxe";
    host.appendChild(box);
    box.addEventListener("change", _dxeOnChange);
    box.addEventListener("click", _dxeOnClick);
  }
  return box;
}

// Called by renderDfsTourney with the board just shown.
function dfsEvalRender(board) {
  const box = _dxeBox();
  if (!box) return;
  if (!board || !board.players || !board.players.length || board.status === "none") { box.innerHTML = ""; return; }
  const same = _dxe.board && _dxe.board.draft_group_id === board.draft_group_id && _dxe.board.kind === board.kind;
  _dxe.board = board;
  if (!same) { _dxe.sel = {}; _dxe.result = null; _dxe.reqId = null; }
  _dxeDraw();
}

function _dxeDraw() {
  const box = _dxeBox(), b = _dxe.board;
  if (!box || !b) return;
  const slots = _dxeSlots(b);
  const pool = (b.players || []).slice().sort((x, y) => (y.salary || 0) - (x.salary || 0));
  const used = new Set(Object.values(_dxe.sel));
  let sal = 0;
  const rows = slots.map((slot, i) => {
    const cur = _dxe.sel[i] || "";
    const p = pool.find((q) => q.name === cur);
    if (p) sal += _dxeSalary(p, slot);
    const opts = pool.filter((q) => _dxeElig(slot, q) && (q.name === cur || !used.has(q.name)))
      .map((q) => `<option value="${_dxeEsc(q.name)}"${q.name === cur ? " selected" : ""}>${_dxeEsc(q.name)} (${_dxeEsc(q.team)} ${_dxeEsc(q.pos)}) $${_dxeSalary(q, slot).toLocaleString()} · ${q.proj ?? "-"}${q.field_only ? " · outside Vigil's gate" : ""}</option>`).join("");
    return `<label class="dxe-slot"><span class="dxe-slotname">${slot}</span><select data-dxe-slot="${i}"><option value="">-</option>${opts}</select></label>`;
  }).join("");
  const filled = slots.every((_s, i) => _dxe.sel[i]);
  const over = sal > 50000;
  const starts = [["top_top1", "top 1%"], ["top_money", "money"], ["top_win", "win"]]
    .filter(([k]) => _dxeBoardRows(b, k).length)
    .map(([k, lbl]) => `<button type="button" class="dxe-start" data-dxe-start="${k}">Start from Vigil's #1 (${lbl})</button>`).join(" ");
  box.innerHTML = `<h3 class="dxe-h">🧪 Evaluate your own lineup</h3>
    <p class="small" style="margin:2px 0 8px;color:var(--muted)">Build any legal lineup from this board's pool (or start from one of Vigil's and swap players). The PC scores it, with Vigil's top lineups beside it, on this slate's <b>final worlds</b>: simulated on their own seed, so nothing on the board was chosen on them. No football is re-simulated for a lineup after the slate's first request.</p>
    <div class="dxe-starts">${starts}</div>
    <div class="dxe-grid">${rows}</div>
    <div class="small dxe-meter${over ? " ev neg" : ""}">Salary $${sal.toLocaleString()} of $50,000 · ${over ? `$${(sal - 50000).toLocaleString()} over` : `$${(50000 - sal).toLocaleString()} left`}</div>
    <div class="dxe-actions"><button type="button" class="primary" data-dxe-go="1"${filled && !over ? "" : " disabled"}>Evaluate on the PC</button>
      <button type="button" data-dxe-clear="1">Clear</button></div>
    <div id="dxeStatus" class="small"></div>
    <div id="dxeResult"></div>`;
  if (_dxe.result) _dxeShow(_dxe.result);
}

function _dxeBoardRows(b, list) {
  if (!b) return [];
  if (b.kind === "classic") {
    const r = b.results || {};
    const first = Object.values(r)[0] || {};
    return first[list] || [];
  }
  return b[list] || [];
}

function _dxeFillFrom(list) {
  const b = _dxe.board, r = _dxeBoardRows(b, list)[0];
  if (!r) return;
  const slots = _dxeSlots(b);
  const left = (r.lineup || []).slice();
  _dxe.sel = {};
  slots.forEach((slot, i) => {
    const k = left.findIndex((p) => (b.kind === "showdown" ? (slot === "CPT" ? p.slot === "CPT" : p.slot !== "CPT") : p.slot === slot));
    if (k >= 0) { _dxe.sel[i] = left[k].name; left.splice(k, 1); }
  });
}

function _dxeOnChange(ev) {
  const t = ev.target;
  if (t && t.dataset && t.dataset.dxeSlot != null) {
    const i = Number(t.dataset.dxeSlot);
    if (t.value) _dxe.sel[i] = t.value; else delete _dxe.sel[i];
    _dxeDraw();
  }
  if (t && t.dataset && t.dataset.dxeContest != null) {
    _dxe.contest = Number(t.value) || 0;
    if (_dxe.result) _dxeShow(_dxe.result);
  }
}

function _dxeOnClick(ev) {
  const t = ev.target.closest ? ev.target.closest("button") : null;
  if (!t || !t.dataset) return;
  if (t.dataset.dxeStart) { _dxeFillFrom(t.dataset.dxeStart); _dxeDraw(); }
  else if (t.dataset.dxeClear) { _dxe.sel = {}; _dxe.result = null; _dxeDraw(); }
  else if (t.dataset.dxeGo) _dxeSubmit();
}

async function _dxeSubmit() {
  const b = _dxe.board, slots = _dxeSlots(b);
  const names = slots.map((_s, i) => _dxe.sel[i]).filter(Boolean);
  const captain = b.kind === "showdown" ? _dxe.sel[0] : null;
  const st = document.getElementById("dxeStatus");
  st.textContent = "Queuing on the PC...";
  try {
    const r = await fetch("/api/dfs/eval", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dg: b.draft_group_id, picked: !!b.picked, lineups: [{ label: "your lineup", names, captain }] }) });
    const d = await r.json();
    if (!r.ok || d.error) { st.textContent = d.error || "could not queue"; return; }
    _dxe.reqId = d.id;
    _dxePoll(0);
  } catch (e) {
    st.textContent = "could not reach the server";
  }
}

function _dxePoll(n) {
  clearTimeout(_dxe.timer);
  const st = document.getElementById("dxeStatus");
  if (!_dxe.reqId || !st) return;
  if (document.hidden) { _dxe.timer = setTimeout(() => _dxePoll(n), 5000); return; }   // never poll a hidden screen
  fetch(`/api/dfs/eval?id=${encodeURIComponent(_dxe.reqId)}`).then((r) => r.json()).then((d) => {
    if (d.status === "done") { _dxe.result = d.result; st.textContent = ""; _dxeShow(d.result); return; }
    if (d.status === "error") { st.innerHTML = `<span class="ev neg">${_dxeEsc(d.error)}</span>`; return; }
    if (d.status === "unknown") { st.textContent = d.why || "the request is gone"; return; }
    const pc = (d.pc || {}).state;
    st.textContent = pc === "off"
      ? "Queued: the PC is off, so this waits for it (nothing large runs on the website)."
      : `Queued ${d.age_s || 0}s: the PC answers within ~15 s; a slate's FIRST request also simulates its final worlds (a few minutes, once).`;
    if (n < 240) _dxe.timer = setTimeout(() => _dxePoll(n + 1), 5000);
  }).catch(() => { if (n < 240) _dxe.timer = setTimeout(() => _dxePoll(n + 1), 8000); });
}

function _dxeShow(res) {
  const box = document.getElementById("dxeResult");
  if (!box || !res) return;
  const rows = res.lineups || [];
  const nC = Math.max(0, ...rows.map((r) => (r.contests || []).length));
  const ci = Math.min(_dxe.contest, Math.max(0, nC - 1));
  const cname = ((rows.find((r) => r.contests) || {}).contests || [])[ci];
  const csel = nC > 1 ? `<select data-dxe-contest="1">${((rows.find((r) => r.contests) || {}).contests || []).map((c, i) => `<option value="${i}"${i === ci ? " selected" : ""}>${_dxeEsc(c.name)}</option>`).join("")}</select>` : "";
  const f = (v, d = 1) => (v == null ? "-" : Number(v).toFixed(d));
  const body = rows.map((r) => {
    const tag = r.source === "custom" ? `<span class="legtag dxe-custom">yours</span>` : `<span class="legtag">${_dxeEsc(r.source.replace("optimizer:", "Vigil "))}</span>`;
    if (!r.legal) {
      return `<tr><td>${tag} ${_dxeEsc(r.label)}</td><td colspan="10" class="ev neg">${(r.problems || []).map(_dxeEsc).join("; ")}</td></tr>`;
    }
    const c = (r.contests || [])[ci] || {};
    const corr = r.corr ? `<details class="small"><summary>correlations</summary><table class="dxe-corr">${r.corr.map((row, i) => `<tr><th>${_dxeEsc((r.names[i] || "").split(" ").slice(-1)[0])}</th>${row.map((v) => `<td style="color:${v > 0.15 ? "var(--yes)" : v < -0.15 ? "var(--no)" : "var(--muted)"}">${v.toFixed(2)}</td>`).join("")}</tr>`).join("")}</table></details>` : "";
    return `<tr><td>${tag} ${_dxeEsc(r.label)}<div class="small" style="color:var(--muted)">${(r.names || []).map(_dxeEsc).join(", ")}${r.captain ? ` · CPT ${_dxeEsc(r.captain)}` : ""}</div>${(r.notes || []).map((n) => `<div class="small" style="color:var(--hold)">${_dxeEsc(n)}</div>`).join("")}${corr}</td>
      <td>$${(r.salary || 0).toLocaleString()}</td><td>${f(r.proj)}</td><td>${f(r.mean)}</td><td>${f(r.p50)}</td><td>${f(r.p90)}</td><td>${f(r.p99)}</td>
      <td><b>${f(c.top1_pct, 2)}%</b></td><td>${f(c.top01_pct, 2)}%</td><td>${f(c.cash_pct, 1)}%</td>
      <td title="experimental: ties not split, rank read off the field's mass">${c.ev_experimental != null ? "$" + Number(c.ev_experimental).toLocaleString() : "-"}</td></tr>`;
  }).join("");
  box.innerHTML = `<div class="small" style="margin:8px 0 4px">${csel} <span style="color:var(--muted)">${_dxeEsc(res.worlds_note || "")}</span></div>
    ${res.development_note ? `<div class="small" style="color:var(--hold);margin-bottom:4px">⚠ ${_dxeEsc(res.development_note)}</div>` : ""}
    ${(res.missing_from_final || []).length ? `<div class="small" style="color:var(--hold)">No final worlds for: ${res.missing_from_final.map(_dxeEsc).join(", ")}</div>` : ""}
    <div style="overflow-x:auto"><table class="small dxe-table"><thead><tr><th style="text-align:left">lineup</th><th>$</th><th>proj</th><th>mean</th><th>P50</th><th>P90</th><th>P99</th><th>top 1%</th><th>top 0.1%</th><th>cash</th><th title="experimental">EV (exp.)</th></tr></thead><tbody>${body}</tbody></table></div>
    <div class="small" style="color:var(--muted);margin-top:4px">${cname ? _dxeEsc(cname.name) + " · " : ""}${(res.worlds || 0).toLocaleString()} final worlds · answered in ${res.seconds ?? "-"}s · look ${res.looks || 1}</div>
    ${res.compute && res.compute.device ? `<div class="small" style="color:var(--muted)">Field thresholds: ${_dxeEsc(res.compute.device)}${res.compute.seconds ? ` in ${_dxeEsc(res.compute.seconds)}s` : ""}${res.compute.worlds ? ` (${Number(res.compute.worlds).toLocaleString()} worlds x ${Number(res.compute.field || 0).toLocaleString()} field lineups)` : ""}</div>` : ""}`;
}
