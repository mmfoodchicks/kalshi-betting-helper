"use strict";
// The player picker (2026-10-30; research/reports/dfs_picker.md). The owner: '"exclude player": (player name)
// ... "force captain": (player) ... force players into a position. Like if I wanted a team with dak, pickens,
// and ceedee lamb. It would create all lineups with those players forced into the lineup. Of course the
// automatic builds would do all available players anywhere.'
// Two places take a pick: the lineup sheet (its pool is the CSV in the box) and Build on the PC (its pool is
// DraftKings' slate). The server checks every pick and its answer is final; this screen helps choose and adds
// up the salary. No inline handlers: one delegated listener, every name escaped, buttons carry pool indices.

const _PK = {
  sheet: { pool: [], key: "", showdown: false, exclude: [], force: [], captain: null, q: "" },
  pc: { pool: [], key: "", showdown: false, exclude: [], force: [], captain: null, q: "", dg: "" },
};
const _PK_CAP = 50000;

function _pkEsc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// One CSV line, quotes honoured (DraftKings' export quotes nothing today; a hand-edited one might).
function _pkSplit(line) {
  const out = [];
  let cur = "", q = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (q) {
      if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; } else if (ch === '"') q = false; else cur += ch;
    } else if (ch === '"') q = true;
    else if (ch === ",") { out.push(cur); cur = ""; } else cur += ch;
  }
  out.push(cur);
  return out.map((x) => x.trim());
}

// The pool a pick names: one row per man (a showdown export lists him twice, FLEX and CPT at 1.5x).
function _pkPool(text) {
  const lines = String(text || "").replace(/^﻿/, "").split(/\r?\n/).filter((l) => l.trim());
  if (!lines.length) return { pool: [], showdown: false };
  const head = _pkSplit(lines[0]).map((h) => h.toLowerCase());
  const col = (n) => head.indexOf(n);
  const iName = col("name"), iId = col("id"), iPos = col("position"), iRp = col("roster position"),
    iSal = col("salary"), iTeam = col("teamabbrev"), iSt = col("status");
  if (iName < 0 || iSal < 0) return { pool: [], showdown: false };
  const by = new Map();
  let showdown = false;
  for (const ln of lines.slice(1)) {
    const c = _pkSplit(ln);
    const name = c[iName] || "";
    if (!name) continue;
    const team = (iTeam >= 0 ? c[iTeam] : "").toUpperCase();
    const rp = (iRp >= 0 ? c[iRp] : "").toUpperCase();
    const sal = parseInt(c[iSal], 10) || 0;
    const key = `${name}|${team}`;
    const row = by.get(key) || { name, team, id: "", pos: ((iPos >= 0 ? c[iPos] : "") || "").split("/")[0].toUpperCase(),
      salary: 0, cpt: 0, status: (iSt >= 0 ? c[iSt] : "").toUpperCase() };
    if (rp === "CPT") { showdown = true; row.cpt = sal; } else { row.salary = sal; row.id = (iId >= 0 ? c[iId] : "") || row.id; }
    if (!row.id && iId >= 0) row.id = c[iId] || "";
    by.set(key, row);
  }
  const pool = [...by.values()].map((r) => Object.assign(r, { salary: r.salary || Math.round((r.cpt || 0) / 1.5),
    cpt: r.cpt || Math.round((r.salary || 0) * 1.5) }));
  pool.sort((a, b) => b.salary - a.salary || a.name.localeCompare(b.name));
  return { pool, showdown };
}

function _pkStoreKey(ctx) {
  const s = _PK[ctx];
  return ctx === "pc" ? `vigil.pick.dg.${s.dg}` : `vigil.pick.sheet.${s.key}`;
}
function _pkSave(ctx) {
  const s = _PK[ctx];
  try {
    localStorage.setItem(_pkStoreKey(ctx), JSON.stringify({ exclude: s.exclude, force: s.force, captain: s.captain }));
  } catch (e) { /* a private window: the picks live for this page only */ }
}
function _pkRestore(ctx) {
  const s = _PK[ctx];
  s.exclude = []; s.force = []; s.captain = null;
  try {
    const v = JSON.parse(localStorage.getItem(_pkStoreKey(ctx)) || "null");
    if (!v) return;
    const on = (p) => p && s.pool.some((r) => r.name === p.name && r.team === p.team);
    s.exclude = (v.exclude || []).filter(on);
    s.force = (v.force || []).filter(on);
    s.captain = s.showdown && on(v.captain) ? v.captain : null;
  } catch (e) { /* nothing saved, or storage blocked */ }
}

function _pkSetPool(ctx, text, key) {
  const s = _PK[ctx];
  if (s.key === key && s.pool.length) return;
  const got = _pkPool(text);
  s.pool = got.pool; s.showdown = got.showdown; s.key = key;
  _pkRestore(ctx);
}

function _pkBox(ctx) { return document.getElementById(ctx === "pc" ? "dfsPickPc" : "dfsPickSheet"); }

// The sheet's pool is whatever CSV sits in the box (the contest picker fills it without an input event, so it
// is re-read whenever the picker is used or drawn).
function dfsPickLoadSheet() {
  const sp = (document.getElementById("dfsSport") || {}).value;
  const box = _pkBox("sheet");
  if (!box) return;
  if (sp !== "nfl") { box.innerHTML = ""; return; }
  const text = (document.getElementById("dfsCsv") || {}).value || "";
  _pkSetPool("sheet", text, `${text.length}:${text.slice(0, 200)}`);
  _pkRender("sheet");
}

function dfsPickLoad(ctx, dg) {
  const s = _PK[ctx];
  if (ctx !== "pc") { dfsPickLoadSheet(); return Promise.resolve(); }
  const next = String(dg || "");
  if (next !== s.dg) {
    // another slate: its picks are its own (restored from storage when its pool lands); until then nothing is
    // picked, so a Build pressed while the pool loads never carries the last slate's picks under this one
    s.exclude = []; s.force = []; s.captain = null; s.pool = []; s.key = "";
  }
  s.dg = next;
  const box = _pkBox("pc");
  if (!box) return Promise.resolve();
  if (!s.dg) { s.pool = []; s.key = ""; box.innerHTML = ""; return Promise.resolve(); }
  // a redraw of the Build line keeps the loaded pool (no refetch painting over the chips)
  if (s.key === `dg:${s.dg}` && s.pool.length) { _pkRender("pc"); return Promise.resolve(); }
  if (s.pending && s.pending.dg === s.dg && !s.pending.done) return s.pending.p;
  box.innerHTML = `<div class="small pk-note">Loading DraftKings' pool for the picker...</div>`;
  const pend = { dg: s.dg, done: false, p: null };
  pend.p = (async () => {
    try {
      const d = await (await fetch(`/api/dfs/slate?sport=nfl&dg=${encodeURIComponent(pend.dg)}`)).json();
      if (s.dg !== pend.dg) return;
      if (d.error || !d.csv) { box.innerHTML = `<div class="small pk-note">${_pkEsc(d.error || "no player pool for this slate")}</div>`; return; }
      _pkSetPool("pc", d.csv, `dg:${pend.dg}`);
      _pkRender("pc");
    } catch (e) { box.innerHTML = `<div class="small pk-note">The player pool did not load.</div>`; }
    finally { pend.done = true; }
  })();
  s.pending = pend;
  return pend.p;
}

// Build on the PC waits for this slate's pool, and the picks saved for it, before it reads the picks: pressed
// while the pool loaded it went out plain and replaced a queued picked Build (round-3 review). A load that
// failed resolves too, so the Build is never stuck behind it.
function dfsPickReady(dg) {
  const s = _PK.pc;
  return s.pending && s.pending.dg === String(dg) ? s.pending.p : Promise.resolve();
}

function _pkIs(list, r) { return list.some((p) => p.name === r.name && p.team === r.team); }
function _pkEntry(r) { return { name: r.name, team: r.team, id: r.id || "" }; }

function _pkCheck(ctx) {
  const s = _PK[ctx];
  const row = (p) => s.pool.find((r) => r.name === p.name && r.team === p.team);
  let sal = 0;
  for (const p of s.force) {
    const r = row(p);
    if (!r) continue;
    sal += s.captain && s.captain.name === p.name && s.captain.team === p.team ? r.cpt : r.salary;
  }
  const bits = [];
  if (s.force.length) bits.push(`${s.force.length} forced: $${sal.toLocaleString()} of $${_PK_CAP.toLocaleString()}`);
  if (s.captain) bits.push(`captain ${_pkEsc(s.captain.name)}`);
  if (s.exclude.length) bits.push(`${s.exclude.length} excluded`);
  return bits.join(" · ");
}

function _pkRender(ctx) {
  const s = _PK[ctx];
  const box = _pkBox(ctx);
  if (!box) return;
  if (!s.pool.length) {
    box.innerHTML = ctx === "sheet"
      ? `<div class="pk-box"><div class="pk-head">🎯 Player picker</div><div class="small pk-note">Load the slate's CSV above (or pick a contest) and its players appear here. A pasted export needs its header row.</div></div>`
      : "";
    return;
  }
  const q = (s.q || "").trim().toLowerCase();
  const hits = q ? s.pool.map((r, i) => [r, i]).filter(([r]) => `${r.name} ${r.team} ${r.pos}`.toLowerCase().includes(q)).slice(0, 8) : [];
  const status = (r) => r.status ? (["OUT", "O", "IR", "PUP", "NFI", "NA", "SUSP", "SUS", "D", "DOUBTFUL"].includes(r.status)
    ? `<span class="pk-out">${_pkEsc(r.status)}</span>` : _pkEsc(r.status)) : `<span class="pk-faint">status unknown</span>`;
  const btn = (act, i, label, on) => `<button type="button" class="track-mini pk-btn${on ? " pk-on" : ""}" data-pk-act="${act}" data-pk-ctx="${ctx}" data-pk-i="${i}">${label}</button>`;
  const res = hits.map(([r, i]) => {
    const isCap = !!(s.captain && s.captain.name === r.name && s.captain.team === r.team);
    return `<div class="pk-row"><span class="pk-who"><b>${_pkEsc(r.name)}</b> · ${_pkEsc(r.pos)} · ${_pkEsc(r.team)} · $${r.salary.toLocaleString()} · ${status(r)}</span>`
      + `<span class="pk-acts">${btn("exclude", i, "Exclude", _pkIs(s.exclude, r))}${btn("force", i, "Force in", _pkIs(s.force, r))}`
      + `${s.showdown ? btn("captain", i, "Captain", isCap) : ""}</span></div>`;
  }).join("");
  const chip = (p, kind) => {
    const i = s.pool.findIndex((r) => r.name === p.name && r.team === p.team);
    const lab = kind === "captain" ? "captain" : kind === "force" ? "in" : "out";
    return `<span class="pk-chip pk-${kind}">${_pkEsc(p.name)} <span class="pk-faint">${lab}</span> <button type="button" class="pk-x" data-pk-act="drop-${kind}" data-pk-ctx="${ctx}" data-pk-i="${i}" title="take this pick back">✕</button></span>`;
  };
  const caps = s.captain ? [chip(s.captain, "captain")] : [];
  const chips = caps.concat(s.force.filter((p) => !(s.captain && p.name === s.captain.name && p.team === s.captain.team)).map((p) => chip(p, "force")),
    s.exclude.map((p) => chip(p, "exclude"))).join("");
  const sub = ctx === "pc"
    ? "This Build only; the PC's own boards use everyone. Checked against DraftKings' pool when queued; Sleeper's projection and depth chart are checked when the PC builds (about ten minutes)."
    : "This Run only. A forced player is in every lineup (in the lineup; the slot does not change a classic score); an excluded one is in none. The field you are scored against still plays him.";
  box.innerHTML = `<div class="pk-box"><div class="pk-head">🎯 Player picker <span class="pk-faint">${s.showdown ? "showdown" : "classic"} · ${s.pool.length} players</span>`
    + `${(s.force.length || s.exclude.length || s.captain) ? ` <button type="button" class="track-mini" data-pk-act="clear" data-pk-ctx="${ctx}" data-pk-i="-1">clear</button>` : ""}</div>`
    + `<div class="small pk-note">${sub}</div>`
    + `<input type="search" class="pk-search" data-pk-search="${ctx}" placeholder="Search a player (name, team or position)" value="${_pkEsc(s.q || "")}" autocomplete="off"/>`
    + `<div class="pk-results">${res || (q ? `<div class="small pk-note">No player matches.</div>` : "")}</div>`
    + `<div class="pk-chips">${chips}</div>`
    + `<div class="small pk-check">${_pkCheck(ctx)}</div></div>`;
}

// The request body's `pick`, or null when nothing is picked (never a bare `exclude`: that key means games). On
// Build on the PC, `dg` is the slate the request is for: picks made on another slate's pool are never sent.
function dfsPickPayload(ctx, dg) {
  const s = _PK[ctx];
  if (ctx === "sheet") dfsPickLoadSheet();
  if (ctx === "pc" && (!s.dg || s.key !== `dg:${s.dg}` || (dg != null && String(dg) !== s.dg))) return null;
  if (!s.exclude.length && !s.force.length && !s.captain) return null;
  return { exclude: s.exclude.map(_pkEntry), force: s.force.map(_pkEntry), captain: s.captain ? _pkEntry(s.captain) : null };
}

function _pkAct(ctx, act, i) {
  const s = _PK[ctx];
  if (act === "clear") { s.exclude = []; s.force = []; s.captain = null; _pkSave(ctx); _pkRender(ctx); return; }
  const r = s.pool[i];
  if (!r) return;
  const same = (p) => p.name === r.name && p.team === r.team;
  const drop = (list) => list.filter((p) => !same(p));
  if (act === "exclude" || act === "drop-exclude") {
    const had = s.exclude.some(same);
    s.exclude = drop(s.exclude);
    if (act === "exclude" && !had) {
      s.exclude.push(_pkEntry(r));
      s.force = drop(s.force);
      if (s.captain && same(s.captain)) s.captain = null;
    }
  } else if (act === "force" || act === "drop-force") {
    const had = s.force.some(same);
    s.force = drop(s.force);
    if (act === "force" && !had) { s.force.push(_pkEntry(r)); s.exclude = drop(s.exclude); }
    if (s.captain && same(s.captain) && !s.force.some(same)) s.captain = null;
  } else if (act === "captain" || act === "drop-captain") {
    const had = !!(s.captain && same(s.captain));
    if (had) { s.captain = null; s.force = drop(s.force); } else if (act === "captain") {
      // the old captain's forced entry was his captaincy's: he leaves with it (force him again to keep him at FLEX)
      const old = s.captain;
      if (old) s.force = s.force.filter((p) => !(p.name === old.name && p.team === old.team));
      s.captain = _pkEntry(r);
      s.exclude = drop(s.exclude);
      if (!s.force.some(same)) s.force.push(_pkEntry(r));
    }
  }
  _pkSave(ctx);
  _pkRender(ctx);
}

// What a picked build says it was built under: the sheet's result and a picked tournament board.
function dfsPickBanner(d) {
  const pk = d && d.pick;
  if (!pk) return "";
  const parts = [];
  if (pk.captain) parts.push(`captain <b>${_pkEsc(pk.captain)}</b>`);
  const forced = (pk.force || []).filter((n) => n !== pk.captain);
  if (forced.length) parts.push(`forced in: <b>${forced.map(_pkEsc).join(", ")}</b>`);
  if ((pk.exclude || []).length) parts.push(`excluded: ${(pk.exclude || []).map(_pkEsc).join(", ")}`);
  const waived = (pk.waived || []).map((w) => `<li>${_pkEsc(w)}</li>`).join("");
  const notes = (pk.notes || []).map((w) => `<li>${_pkEsc(w)}</li>`).join("");
  const best = d.unpicked_best != null ? ` The best lineup without your picks projects ${_pkEsc(d.unpicked_best)}.` : "";
  return `<div class="pk-banner">🎯 Built with your picks: ${parts.join(" · ")}.${best}`
    + `${waived ? `<div class="small">House rules your picks override:<ul>${waived}</ul></div>` : ""}`
    + `${notes ? `<div class="small"><ul>${notes}</ul></div>` : ""}</div>`;
}

document.addEventListener("click", (e) => {
  const b = e.target.closest ? e.target.closest("[data-pk-act]") : null;
  if (!b) return;
  e.preventDefault();
  _pkAct(b.getAttribute("data-pk-ctx"), b.getAttribute("data-pk-act"), parseInt(b.getAttribute("data-pk-i"), 10));
});
document.addEventListener("input", (e) => {
  const t = e.target;
  if (!t || !t.getAttribute) return;
  // a paste or an edit in the CSV box: the picker shows that pool at once (it re-parses only when the box changed)
  if (t.id === "dfsCsv") { dfsPickLoadSheet(); return; }
  const ctx = t.getAttribute("data-pk-search");
  if (!ctx) return;
  if (ctx === "sheet") {
    const text = (document.getElementById("dfsCsv") || {}).value || "";
    _pkSetPool("sheet", text, `${text.length}:${text.slice(0, 200)}`);
  }
  _PK[ctx].q = t.value;
  const pos = t.selectionStart;
  _pkRender(ctx);
  const again = document.querySelector(`[data-pk-search="${ctx}"]`);
  if (again) { again.focus(); try { again.setSelectionRange(pos, pos); } catch (err) { /* type=search may refuse */ } }
});
document.addEventListener("change", (e) => {
  const t = e.target;
  if (!t) return;
  if (t.id === "dfsTourneyPickBuild") dfsPickLoad("pc", t.value);
  if (t.id === "dfsSport") dfsPickLoadSheet();
});
document.addEventListener("focusin", (e) => {
  const t = e.target;
  if (t && t.getAttribute && t.getAttribute("data-pk-search") === "sheet") {
    const text = (document.getElementById("dfsCsv") || {}).value || "";
    if (_PK.sheet.key !== `${text.length}:${text.slice(0, 200)}`) {
      _pkSetPool("sheet", text, `${text.length}:${text.slice(0, 200)}`);
      _pkRender("sheet");
      const again = document.querySelector('[data-pk-search="sheet"]');
      if (again) again.focus();
    }
  }
});
