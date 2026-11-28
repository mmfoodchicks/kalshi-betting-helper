"use strict";
// The player picker (2026-10-30; research/reports/dfs_picker.md). The owner: '"exclude player": (player name)
// ... "force captain": (player) ... force players into a position. Like if I wanted a team with dak, pickens,
// and ceedee lamb. It would create all lineups with those players forced into the lineup. Of course the
// automatic builds would do all available players anywhere.'
// Two places take a pick: the lineup sheet (its pool is the CSV in the box) and Build on the PC (its pool is
// DraftKings' slate). The server checks every pick and its answer is final; this screen helps choose and adds
// up the salary. No inline handlers: one delegated listener, every name escaped, buttons carry pool indices.

const _PK = {
  sheet: { pool: [], key: "", showdown: false, exclude: [], force: [], captain: null, q: "", rules: {}, port: {}, more: false },
  pc: { pool: [], key: "", showdown: false, exclude: [], force: [], captain: null, q: "", dg: "", rules: {}, port: {}, more: false },
};
const _PK_CAP = 50000;
// The owner's roster rules and portfolio caps (2026-11-26; dfs_portfolio.py): values as the inputs hold them, ""
// for unset. They ride the same `pick` as the names and bind OUR lineups only -- the server says so on the result.
const _PK_RULE_KEYS = ["stack", "bring", "team_max", "min_salary"];
const _PK_PORT_KEYS = ["player_pct", "team_pct", "max_overlap"];
// The eligibility modes (dfs_eligibility; the owner approved the selector 2026-11-27). Conservative is the
// production default and the absence of the key; the other two are experimental and always say so.
const _PK_MODES = [["", "Conservative (production default)"], ["adaptive", "Adaptive (experimental)"],
  ["unrestricted", "Unrestricted (experimental)"]];

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
    localStorage.setItem(_pkStoreKey(ctx), JSON.stringify({ exclude: s.exclude, force: s.force, captain: s.captain,
      rules: s.rules, port: s.port, mode: s.mode || "" }));
  } catch (e) { /* a private window: the picks live for this page only */ }
}
function _pkRestore(ctx) {
  const s = _PK[ctx];
  s.exclude = []; s.force = []; s.captain = null; s.rules = {}; s.port = {}; s.mode = "";
  try {
    const v = JSON.parse(localStorage.getItem(_pkStoreKey(ctx)) || "null");
    if (!v) return;
    const on = (p) => p && s.pool.some((r) => r.name === p.name && r.team === p.team);
    s.exclude = (v.exclude || []).filter(on);
    s.force = (v.force || []).filter(on);
    s.captain = s.showdown && on(v.captain) ? v.captain : null;
    const pick = (o, keys) => { const out = {}; for (const k of keys) if (o && o[k] != null && o[k] !== "") out[k] = String(o[k]); return out; };
    s.mode = ctx === "pc" && _PK_MODES.some((m) => m[0] === v.mode) ? v.mode : "";
    s.rules = pick(v.rules, _PK_RULE_KEYS);
    s.port = pick(v.port, _PK_PORT_KEYS);
    s.port.players = ((v.port || {}).players || []).filter(on).map((p) => ({ name: p.name, team: p.team, id: p.id || "", max_pct: String(p.max_pct) }));
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
    s.exclude = []; s.force = []; s.captain = null; s.pool = []; s.key = ""; s.rules = {}; s.port = {};
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
  const nr = _pkSetCount(ctx);
  if (nr) bits.push(`${nr} rule${nr > 1 ? "s" : ""} or cap${nr > 1 ? "s" : ""} set`);
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
      + `${s.showdown ? btn("captain", i, "Captain", isCap) : ""}${btn("cap", i, "Cap", _pkIs(s.port.players || [], r))}</span></div>`;
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
    + `${(s.force.length || s.exclude.length || s.captain || _pkSetCount(ctx)) ? ` <button type="button" class="track-mini" data-pk-act="clear" data-pk-ctx="${ctx}" data-pk-i="-1">clear</button>` : ""}</div>`
    + `<div class="small pk-note">${sub}</div>`
    + `<input type="search" class="pk-search" data-pk-search="${ctx}" placeholder="Search a player (name, team or position)" value="${_pkEsc(s.q || "")}" autocomplete="off"/>`
    + `<div class="pk-results">${res || (q ? `<div class="small pk-note">No player matches.</div>` : "")}</div>`
    + `<div class="pk-chips">${chips}</div>`
    + _pkControls(ctx)
    + `<div class="small pk-check">${_pkCheck(ctx)}</div></div>`;
}

// How many roster rules and portfolio caps are set (per-player caps count one each).
function _pkSetCount(ctx) {
  const s = _PK[ctx];
  const r = s.rules || {}, po = s.port || {};
  return _PK_RULE_KEYS.filter((k) => r[k] && !(s.showdown && (k === "stack" || k === "bring"))).length
    + _PK_PORT_KEYS.filter((k) => po[k] != null && po[k] !== "").length + (po.players || []).length;
}

// The rules and caps, folded under one line: every control is a labelled select or number box in a wrapping row,
// so it fits a phone. A change re-draws the picker (data-pk-set, the delegated listener below).
function _pkControls(ctx) {
  const s = _PK[ctx];
  const r = s.rules || {}, po = s.port || {};
  const sel = (grp, key, opts, val) => `<select data-pk-set="${grp}.${key}" data-pk-ctx="${ctx}" style="width:auto;max-width:100%;padding:4px 6px">`
    + opts.map(([v, l]) => `<option value="${_pkEsc(v)}"${String(val == null ? "" : val) === String(v) ? " selected" : ""}>${_pkEsc(l)}</option>`).join("") + `</select>`;
  const num = (grp, key, val, lo, hi, step) => `<input type="number" inputmode="numeric" data-pk-set="${grp}.${key}" data-pk-ctx="${ctx}" min="${lo}" max="${hi}" step="${step}" value="${_pkEsc(val == null ? "" : val)}" placeholder="none" style="width:6.5em;padding:4px 6px"/>`;
  const lab = (txt, ctl) => `<label style="flex:0 1 auto;min-width:0">${_pkEsc(txt)}${ctl}</label>`;
  const row = (xs) => `<div style="display:flex;flex-wrap:wrap;gap:6px 12px;align-items:flex-end;margin:4px 0 8px">${xs.join("")}</div>`;
  const size = s.showdown ? 6 : 9;
  const roster = [];
  if (!s.showdown) {
    roster.push(lab("Stack: QB +", sel("rules", "stack", [["", "house rule"], ["1", "1 receiver"], ["2", "2 receivers"], ["3", "3 receivers"], ["4", "4 receivers"]], r.stack)));
    roster.push(lab("Bring-back", sel("rules", "bring", [["", "either"], ["required", "required"], ["forbidden", "none"]], r.bring)));
  }
  const teamMax = s.showdown ? [3, 4, 5] : [1, 2, 3, 4, 5, 6, 7, 8];
  roster.push(lab("Max per team", sel("rules", "team_max", [["", "any"]].concat(teamMax.map((n) => [String(n), String(n)])), r.team_max)));
  roster.push(lab("Min salary $", num("rules", "min_salary", r.min_salary, 0, _PK_CAP, 100)));
  const ov = [["", "any"]];
  for (let n = 0; n < size; n++) ov.push([String(n), `${n} of ${size}`]);
  const port = [
    lab("Max per player %", num("port", "player_pct", po.player_pct, 1, 100, 1)),
    lab(s.showdown ? "Max per captain's team %" : "Max per QB's team %", num("port", "team_pct", po.team_pct, 1, 100, 1)),
    lab("Max shared players", sel("port", "max_overlap", ov, po.max_overlap)),
  ];
  const caps = (po.players || []).map((p) => {
    const i = s.pool.findIndex((x) => x.name === p.name && x.team === p.team);
    return `<span class="pk-chip">${_pkEsc(p.name)} <span class="pk-faint">at most</span> <input type="number" inputmode="numeric" data-pk-cap="${i}" data-pk-ctx="${ctx}" min="0" max="100" step="1" value="${_pkEsc(p.max_pct)}" style="width:4.2em;padding:2px 4px" aria-label="exposure cap for ${_pkEsc(p.name)}"/>% <button type="button" class="pk-x" data-pk-act="drop-cap" data-pk-ctx="${ctx}" data-pk-i="${i}" title="take this cap back">✕</button></span>`;
  }).join("");
  const n = _pkSetCount(ctx);
  const where = ctx === "pc" ? "the board's portfolio (held at 5, 10 and 20 entries)" : "a Run of several lineups";
  const mode = ctx === "pc" ? `<div class="small" style="margin:4px 0">${lab("Eligibility: ", sel("opt", "mode", _PK_MODES, s.mode || ""))}`
    + (s.mode ? ` <b style="color:#e0a656">🧪 experimental: ${_pkEsc(s.mode)} widens OUR lineups past the depth chart (the field and the projections are unchanged); production builds stay conservative until the registered test says otherwise</b>` : ` <span class="pk-faint">conservative is the production default</span>`)
    + `</div>` : "";
  return mode + `<details class="pk-more"${s.more ? " open" : ""}><summary class="small" data-pk-more="${ctx}" style="cursor:pointer;margin:4px 0">Roster rules and portfolio caps${n ? ` <b>(${n} set)</b>` : ""}</summary>`
    + `<div class="small pk-note">Your lineups only: the field you are scored against is never restricted. DraftKings' rules always hold, and the house rules bend only for players you force.${s.showdown ? " The stack and bring-back rules are classic rules." : ""}</div>`
    + `<div class="small"><b>Every lineup</b></div>${row(roster)}`
    + `<div class="small"><b>Portfolio caps</b> <span class="pk-faint">${_pkEsc(where)}; a share of the entries, at least one entry for any cap above 0</span></div>${row(port)}`
    + (caps ? `<div class="pk-chips">${caps}</div>` : `<div class="small pk-faint">A cap for one player: press Cap beside him above.</div>`)
    + `</details>`;
}

// The request body's `pick`, or null when nothing is picked (never a bare `exclude`: that key means games). On
// Build on the PC, `dg` is the slate the request is for: picks made on another slate's pool are never sent.
// `rules` and `portfolio` ride only when set (the server reads a pick without them exactly as before).
function dfsPickPayload(ctx, dg) {
  const s = _PK[ctx];
  if (ctx === "sheet") dfsPickLoadSheet();
  if (ctx === "pc" && (!s.dg || s.key !== `dg:${s.dg}` || (dg != null && String(dg) !== s.dg))) return null;
  const r = s.rules || {}, po = s.port || {};
  const rules = {}, port = {};
  for (const k of _PK_RULE_KEYS) {
    if (!r[k] || (s.showdown && (k === "stack" || k === "bring"))) continue;
    rules[k] = k === "bring" ? r[k] : Number(r[k]);
  }
  for (const k of _PK_PORT_KEYS) if (po[k] != null && po[k] !== "") port[k] = Number(po[k]);
  if ((po.players || []).length) port.players = po.players.map((p) => Object.assign(_pkEntry(p), { max_pct: Number(p.max_pct) }));
  const hasR = Object.keys(rules).length > 0, hasP = Object.keys(port).length > 0;
  const mode = ctx === "pc" && s.mode ? s.mode : "";
  if (!s.exclude.length && !s.force.length && !s.captain && !hasR && !hasP && !mode) return null;
  const out = { exclude: s.exclude.map(_pkEntry), force: s.force.map(_pkEntry), captain: s.captain ? _pkEntry(s.captain) : null };
  if (mode) out.mode = mode;
  if (hasR) out.rules = rules;
  if (hasP) out.portfolio = port;
  return out;
}

function _pkAct(ctx, act, i) {
  const s = _PK[ctx];
  if (act === "clear") { s.exclude = []; s.force = []; s.captain = null; s.rules = {}; s.port = {}; _pkSave(ctx); _pkRender(ctx); return; }
  const r = s.pool[i];
  if (!r) return;
  const same = (p) => p.name === r.name && p.team === r.team;
  const drop = (list) => list.filter((p) => !same(p));
  if (act === "cap" || act === "drop-cap") {
    // a per-player exposure cap starts at the player-wide cap (or half the entries) and is edited on its chip
    const had = (s.port.players || []).some(same);
    s.port.players = drop(s.port.players || []);
    if (act === "cap" && !had) s.port.players.push(Object.assign(_pkEntry(r), { max_pct: String(s.port.player_pct || 50) }));
    if (act === "cap") s.more = true;
  } else if (act === "exclude" || act === "drop-exclude") {
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
  if ((pk.rules_text || []).length) parts.push(`your rules: ${pk.rules_text.map(_pkEsc).join("; ")}`);
  const po = pk.portfolio;
  if (po) {
    const caps = [];
    if (po.player_pct != null) caps.push(`at most ${_pkEsc(po.player_pct)}% of entries per player`);
    for (const [nm, v] of Object.entries(po.players || {})) caps.push(`${_pkEsc(nm)} at most ${_pkEsc(v)}%`);
    if (po.team_pct != null) caps.push(`at most ${_pkEsc(po.team_pct)}% built around one team`);
    if (po.max_overlap != null) caps.push(`at most ${_pkEsc(po.max_overlap)} players shared by two entries`);
    if (caps.length) parts.push(`portfolio caps: ${caps.join("; ")}`);
  }
  const waived = (pk.waived || []).map((w) => `<li>${_pkEsc(w)}</li>`).join("");
  const notes = (pk.notes || []).map((w) => `<li>${_pkEsc(w)}</li>`).join("");
  const best = d.unpicked_best != null ? ` The best lineup without your picks projects ${_pkEsc(d.unpicked_best)}.` : "";
  return `<div class="pk-banner">🎯 Built with your picks: ${parts.join(" · ")}.${best}`
    + `${waived ? `<div class="small">House rules your picks override:<ul>${waived}</ul></div>` : ""}`
    + `${notes ? `<div class="small"><ul>${notes}</ul></div>` : ""}</div>`;
}

document.addEventListener("click", (e) => {
  // the rules and caps fold: remember whether it is open, so a re-draw after a change keeps it so
  const m = e.target.closest ? e.target.closest("[data-pk-more]") : null;
  if (m) {
    const ctx = m.getAttribute("data-pk-more"), det = m.closest("details");
    if (_PK[ctx] && det) _PK[ctx].more = !det.open;
    return;
  }
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
  const set = t.getAttribute ? t.getAttribute("data-pk-set") : null;
  const cap = t.getAttribute ? t.getAttribute("data-pk-cap") : null;
  if (set || cap != null) {
    const ctx = t.getAttribute("data-pk-ctx");
    const s = _PK[ctx];
    if (!s) return;
    if (set) {
      const [grp, key] = set.split(".");
      if (grp === "opt") s.mode = _PK_MODES.some((m) => m[0] === t.value) ? t.value : "";
      else {
        const bag = grp === "rules" ? s.rules : s.port;
        if (t.value === "" || t.value == null) delete bag[key]; else bag[key] = String(t.value);
      }
    } else {
      const r = s.pool[parseInt(cap, 10)];
      const p = r && (s.port.players || []).find((x) => x.name === r.name && x.team === r.team);
      if (p) p.max_pct = String(t.value === "" ? 0 : t.value);
    }
    s.more = true;
    _pkSave(ctx);
    _pkRender(ctx);
  }
});

// ---- Concentration (2026-11-26) -----------------------------------------------------------------------------
// How concentrated a set of entries is: the same definition as dfs_portfolio.concentration (a guard runs both on
// the same rows), computed here for whatever prefix of a portfolio the tab shows. Rows are the board's or the
// sheet's entry rows ({lineup: [{name, team, slot}]}); the lead team is the captain's, else the quarterback's.
// Percentages round half up in integers on both sides, so the two agree to the digit.
function _pkLead(r) {
  const lu = (r && r.lineup) || [];
  for (const slot of ["CPT", "QB"]) {
    const p = lu.find((q) => q.slot === slot);
    if (p) return p.team || "";
  }
  return "";
}
function dfsConcentration(rows, forced, warnPct) {
  const fz = new Set(forced || []);
  const warn = warnPct == null ? 50 : Number(warnPct);
  const sets = [], teamOf = new Map(), leadN = new Map(), perTeam = new Map();
  for (const r of rows || []) {
    const names = [];
    for (const p of (r.lineup || [])) {
      const nm = p.name;
      if (nm == null || names.includes(nm)) continue;
      names.push(nm);
      if (!teamOf.has(nm)) teamOf.set(nm, p.team || "");
      perTeam.set(p.team || "", (perTeam.get(p.team || "") || 0) + 1);
    }
    sets.push(new Set(names));
    const ld = _pkLead(r);
    leadN.set(ld, (leadN.get(ld) || 0) + 1);
  }
  const n = sets.length;
  const expo = new Map();
  for (const st of sets) for (const nm of st) expo.set(nm, (expo.get(nm) || 0) + 1);
  const size = sets.reduce((m, st) => Math.max(m, st.size), 0);
  let best = 0, pair = null;
  for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
    let o = 0;
    for (const nm of sets[i]) if (sets[j].has(nm)) o++;
    if (o > best) { best = o; pair = [i + 1, j + 1]; }
  }
  const pct = (c) => (n ? Math.floor((2000 * c + n) / (2 * n)) / 10 : 0);
  const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  const players = [...expo.entries()].sort((a, b) => b[1] - a[1] || cmp(a[0], b[0])).map(([nm, c]) => ({
    name: nm, team: teamOf.get(nm) || "", n: c, pct: pct(c), forced: fz.has(nm),
    flag: n >= 3 && !fz.has(nm) && 100 * c > warn * n }));
  const teamSet = new Set([...perTeam.keys(), ...leadN.keys()]);
  const teams = [...teamSet].filter((t) => t).sort((a, b) => (leadN.get(b) || 0) - (leadN.get(a) || 0)
    || (perTeam.get(b) || 0) - (perTeam.get(a) || 0) || cmp(a, b)).map((t) => ({
    team: t, lead_n: leadN.get(t) || 0, lead_pct: pct(leadN.get(t) || 0),
    players_avg: n ? Math.floor((200 * (perTeam.get(t) || 0) + n) / (2 * n)) / 100 : 0 }));
  const flags = players.filter((p) => p.flag).map((p) => `${p.name} is in ${p.n} of ${n} entries (${p.pct}%), above ${warn}%`);
  if (n >= 3 && size && best >= size - 1) flags.push(`entries ${pair[0]} and ${pair[1]} share ${best} of ${size} players`);
  return { entries: n, size, warn_pct: warn, players: players.slice(0, 30), teams, max_overlap: best, overlap_pair: pair, flags };
}
// The concentration under a portfolio: the most-held players, the team each entry is built around, the largest
// overlap, any flag -- and, on a capped board, what the caps did (`lim`, the board's limits report).
function dfsConcentrationHtml(c, lim) {
  if (!c || !c.entries) return "";
  const chip = (p) => `<span class="pk-chip" style="padding:1px 8px${p.flag ? ";border-color:var(--no)" : ""}">${_pkEsc(p.name)} <b>${_pkEsc(p.pct)}%</b>${p.forced ? ' <span class="pk-faint">forced</span>' : ""}</span>`;
  const shown = c.players.filter((p) => p.n >= 2 || p.flag).slice(0, 12);
  const teams = c.teams.filter((t) => t.lead_n > 0).map((t) => `${_pkEsc(t.team)} ${_pkEsc(t.lead_pct)}%`).join(" · ");
  const lead = c.size === 6 ? "captain's team" : "quarterback's team";
  let limTxt = "";
  if (lim) {
    const by = Object.keys(lim.stopped_by || {}).join(", ");
    limTxt = lim.short
      ? `<div style="color:var(--no)">⚠ Your caps left ${_pkEsc(lim.entries)} of ${_pkEsc(lim.k)} entries: nothing left on the shortlist fits them${by ? ` (refused by the ${_pkEsc(by)} cap${by.includes(",") ? "s" : ""})` : ""}.</div>`
      : `<div>Your caps held; they changed ${_pkEsc(lim.caps_changed_picks)} of the ${_pkEsc(lim.entries)} picks.</div>`;
  }
  const flags = (c.flags || []).map((f) => `<li>${_pkEsc(f)}</li>`).join("");
  return `<div class="small" style="margin-top:6px;padding:6px 8px;border:1px solid var(--border);border-radius:8px">`
    + `<div><b>Concentration</b> <span class="pk-faint">${_pkEsc(c.entries)} entries · flagged above ${_pkEsc(c.warn_pct)}%</span></div>`
    + (shown.length ? `<div class="pk-chips">${shown.map(chip).join("")}</div>` : `<div class="pk-faint">No player is in more than one entry.</div>`)
    + (teams ? `<div>Built around (${lead}): ${teams}</div>` : "")
    + `<div>Most players two entries share: <b>${_pkEsc(c.max_overlap)}</b> of ${_pkEsc(c.size)}${c.overlap_pair ? ` (entries ${_pkEsc(c.overlap_pair[0])} and ${_pkEsc(c.overlap_pair[1])})` : ""}</div>`
    + limTxt
    + (flags ? `<ul style="margin:2px 0 0 16px;padding:0;color:var(--no)">${flags}</ul>` : "")
    + `</div>`;
}
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
