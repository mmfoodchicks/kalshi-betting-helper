// ---- NFL game viewer -----------------------------------------------------------
// The owner (2026-10-29): "view the most average play ... for every game ... just
// being able to view play by play", tapped from the model screen, and "simulate
// where the defense players are ... interceptions" -- missed tackles left out on
// his word (the engine has no contact model).
//
// One game the deep engine played BEFORE kickoff (nfl_viewer.py): the most typical
// of 5,000 simulations of the matchup, or ten others, told play by play. Who
// carries, catches, kicks and tackles is drawn by each player's share of Sleeper's
// projections for the week; the dots on the field are drawn for looks -- the
// engine simulates outcomes, not where anyone stands -- and the screen says both.
// Nothing here computes a number: every figure comes from the piece.
//
// No inline event handlers: every control carries data-nv and one delegated
// listener reads it (matchups.js's rule: an id inside an inline click handler is
// decoded by the attribute parser before the script runs). Every server string
// is escaped.
"use strict";

const _NV = {
  el: null, g: null, week: 0, home: "", away: "", k: 0, head: null, ev: [], i: -1,
  playing: false, speed: 1, timer: null, raf: null, tab: "plays", poll: null, polls: 0, req: 0,
};
const _NV_KICKS = { ko: 1, fk: 1, punt: 1, fg: 1 };
const _NV_SNAPS = { run: 1, cmp: 1, inc: 1, int: 1, sack: 1, kneel: 1, pen: 1 };
const _NV_BANNER = {
  td: "TOUCHDOWN", int: "INTERCEPTION", fum: "FUMBLE", fg: "FIELD GOAL", fgx: "NO GOOD", blk: "BLOCKED",
  sf: "SAFETY", tod: "TURNOVER ON DOWNS", "2mw": "TWO-MINUTE WARNING", ot: "OVERTIME", fin: "FINAL",
  onside: "ONSIDE KICK",
};
// What the engine plays and what it does not (Fable's review, m1 and its section 7).
const _NV_NOT_MODELLED = [
  "Timeouts, challenges, spikes, fakes and in-game injuries.",
  "Returns, fair catches and touchbacks: a kickoff or punt is a spot (or a block, or a return touchdown).",
  "Penalties after the snap (holding, interference): a penalty is a no-play with its yards; a penalty on a play is folded into that play's yards, so a run can move the chains on a hold the text never names.",
  "Fumbles the offence recovers and fumble-return touchdowns: a fumble here is a lost one.",
  "Missed tackles, pressures, pass breakups and who blocked a kick.",
  "Weather, personnel groupings and where anyone stands: the dots are a stock formation, drawn for looks.",
  "Interception and fumble returns: the engine draws where the defence takes over, not how far the ball was carried.",
  "About one sack in twelve nets yards (a penalty enforced on the play, as the engine was fitted; most are a known fitting error on fourth-down turnovers, reported 2026-10-29), marked \"artifact\". The same error sets a failed fourth down's spot: a stopped fourth-down run can lose 15 yards, and one in a team's own territory can read as a conversion.",
];

// The tag on a play the engine told from a fitted-table artifact (nfl_viewer.tell's
// `art`: a sack that nets yards; Fable's build review M1).
const _NV_ART = ` <span class="nv-share" title="A sack that nets yards: a fitting error in the engine's tables (failed fourth downs read in the other team's frame), reported 2026-10-29 and not yet refitted. See Not modelled.">artifact</span>`;

function _nvEsc(x) {
  return String(x == null ? "" : x).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function _nvTeamCode(x) { return String(x || "").toUpperCase().replace(/[^A-Z]/g, "").slice(0, 4); }
function _nvMT(ts) {
  if (!ts) return "";
  try {
    return new Date(ts * 1000).toLocaleString("en-US", { timeZone: "America/Denver", weekday: "short",
      hour: "numeric", minute: "2-digit" }) + " MT";
  } catch (e) { return ""; }
}
function _nvClock(c) {
  c = Math.max(0, c | 0);
  return `${Math.floor(c / 60)}:${String(c % 60).padStart(2, "0")}`;
}
function _nvQ(q) { return q <= 4 ? `Q${q}` : (q === 5 ? "OT" : `OT${q - 4}`); }
function _nvOrd(d) { return ["", "1st", "2nd", "3rd", "4th"][d] || `${d}th`; }
function _nvSpot(teams, side, ytg) {
  if (ytg === 50) return "50";
  return ytg > 50 ? `${teams[side]} ${100 - ytg}` : `${teams[1 - side]} ${ytg}`;
}

// ---- opening -----------------------------------------------------------------------
function nvOpen(home, away) {
  const d = (typeof _nflWeekData !== "undefined") ? _nflWeekData : null;
  const g = d && (d.games || []).find((x) => x.home === home && x.away === away);
  if (!d || !g) return;
  _NV.g = g; _NV.week = d.week; _NV.home = home; _NV.away = away; _NV.k = 0; _NV.polls = 0;
  _nvEnsureShell();
  _NV.el.classList.remove("hidden");
  document.body.classList.add("nv-lock");
  _nvLoad();
}
function nvClose() {
  _nvStop();
  clearTimeout(_NV.poll);
  _NV.req++;
  if (_NV.el) _NV.el.classList.add("hidden");
  document.body.classList.remove("nv-lock");
}
function _nvEnsureShell() {
  if (_NV.el) return;
  const el = document.createElement("div");
  el.id = "nvOverlay";
  el.className = "nv-ov hidden";
  el.setAttribute("role", "dialog");
  el.setAttribute("aria-modal", "true");
  el.setAttribute("aria-label", "Simulated game, play by play");
  document.body.appendChild(el);
  _NV.el = el;
}
function _nvLoad() {
  _nvStop();
  clearTimeout(_NV.poll);
  const req = ++_NV.req;
  const g = _NV.g;
  _NV.el.innerHTML = `<div class="nv-box"><div class="nv-top"><div class="nv-title">${_nvEsc(g.away_name || g.away)} @ ${_nvEsc(g.home_name || g.home)}</div>
    <button class="nv-x" type="button" data-nv="close" aria-label="Close">✕</button></div>
    <div class="nv-wait">Loading the simulated game…</div></div>`;
  const url = `/api/nfl/viewer?week=${encodeURIComponent(_NV.week)}&home=${encodeURIComponent(_NV.home)}` +
    `&away=${encodeURIComponent(_NV.away)}&k=${encodeURIComponent(_NV.k)}`;
  fetch(url).then((r) => r.json().then((j) => [r.status, j])).then(([st, j]) => {
    if (req !== _NV.req) return;
    if (st === 202 && j && j.building) {
      _NV.polls++;
      const w = _NV.el.querySelector(".nv-wait");
      if (_NV.polls < 30) {
        if (w) w.textContent = `${j.note || "Simulating…"}${_NV.polls > 1 ? ` (checked ${_NV.polls} times)` : ""}`;
        _NV.poll = setTimeout(_nvLoad, 8000);
      } else if (w) {
        // four minutes of 202s: the server's child failed or is stuck (it is in
        // the error log as NFLV-child / NFLV-server), never "simulating" forever
        w.textContent = "Not built after four minutes: the server's build may have failed (it is in the error log). Close this and tap the game again later.";
      }
      return;
    }
    _NV.polls = 0;
    if (st !== 200 || !j || !j.head || !j.game) {
      const w = _NV.el.querySelector(".nv-wait");
      if (w) w.textContent = (j && j.error) || "The simulated game is not available.";
      return;
    }
    _NV.head = j.head;
    _NV.ev = j.game.events || [];
    _NV.box = j.game.box || null;
    _NV.i = -1;
    _nvRender();
    _nvShow(0, false);
  }).catch(() => {
    if (req !== _NV.req) return;
    const w = _NV.el.querySelector(".nv-wait");
    if (w) w.textContent = "The simulated game could not be loaded.";
  });
}

// ---- the frame -------------------------------------------------------------------------
function _nvRender() {
  const h = _NV.head, g = _NV.g;
  const teams = [h.home, h.away];
  const card = g || {};
  // The header's probability is the BOARD's deep figure (the card's), never a
  // rate of the viewer's own games: one model, one number (Fable's M1).
  const ph = card.p_home_deep != null ? card.p_home_deep : (h.board || {}).p_home;
  const eh = card.exp_home_deep != null ? card.exp_home_deep : (h.board || {}).exp_home;
  const ea = card.exp_away_deep != null ? card.exp_away_deep : (h.board || {}).exp_away;
  const fav = ph >= 0.5 ? h.home : h.away;
  const favP = ph >= 0.5 ? ph : 1 - ph;
  const sim = (h.sims || [])[h.k] || {};
  const isTyp = sim.kind === "typical";
  const pick = h.pickem ? "a pick'em (the median margin is 0), so either side may win it"
    : `the winner is the side the median margin favours (${_nvEsc(h.side > 0 ? h.home : h.away)})`;
  const which = isTyp
    ? `<b>The typical game</b>: of ${Number(h.n_sims || 0).toLocaleString()} simulated games, the one nearest the medians of margin, total, each side's passing and rushing yards, turnovers and plays; ${pick}.`
    : `<b>Another sim (${h.k} of ${(h.sims || []).length - 1})</b>: one of the first simulated games, as random as any, not the typical one.`;
  // When the game shown is won by the side the board does not favour (a pick'em,
  // or a random sim), say both in one line so the winner never reads as the pick.
  const mg = (sim.summary || [0])[0] || 0;
  const winner = mg > 0 ? h.home : mg < 0 ? h.away : null;
  const against = ph != null && winner && winner !== fav
    ? ` In this game ${_nvEsc(winner)} wins; the board's deep engine has ${_nvEsc(fav)} at ${(favP * 100).toFixed(1)}%.` : "";
  // How many simulated games a figure rests on: the PC plays 5,000, the server's
  // fallback 600 (nfl_game_sim.deep_n), so two builds can differ by dice alone.
  const sims = (n) => (Number(n) > 0 ? `${Number(n).toLocaleString()} sims` : "");
  const nShown = card.p_home_deep != null ? card.deep_n_sims : (h.board || {}).n;
  const own = h.board && h.board.p_home != null && card.p_home_deep != null
    && Math.abs(h.board.p_home - card.p_home_deep) >= 0.01
    ? ` · this build's own read ${_nvEsc(h.board.p_home >= 0.5 ? h.home : h.away)} ${((h.board.p_home >= 0.5 ? h.board.p_home : 1 - h.board.p_home) * 100).toFixed(1)}% (${sims(h.board.n) ? `${sims(h.board.n)}, ` : ""}built ${_nvEsc(_nvMT(h.built_ts))}); the board's is from its own build${sims(card.deep_n_sims) ? ` (${sims(card.deep_n_sims)})` : ""}` : "";
  const f1 = (x) => (x == null || isNaN(Number(x)) ? "–" : Number(x).toFixed(1));
  const startedNote = h.started
    ? `<div class="nv-started">The real game has kicked off. This is the PRE-GAME simulation, not the live game.</div>` : "";
  const missing = ((h.names || {}).missing || []).length
    ? ` Without a name this week: ${_nvEsc(h.names.missing.join(", "))}.` : "";
  _NV.el.innerHTML = `<div class="nv-box">
    <div class="nv-top">
      <div><div class="nv-title">${_nvEsc(h.away_name)} @ ${_nvEsc(h.home_name)} <span class="nv-tag">simulated</span></div>
      <div class="nv-sub">Simulated before kickoff · built ${_nvEsc(_nvMT(h.built_ts))}${h.by ? ` (${_nvEsc(h.by)})` : ""}${h.kickoff_ts ? ` · kickoff ${_nvEsc(_nvMT(h.kickoff_ts))}` : ""}</div>
      ${ph != null ? `<div class="nv-sub">The board's deep engine: <b>${_nvEsc(fav)} ${(favP * 100).toFixed(1)}%</b>${sims(nShown) ? ` (${sims(nShown)})` : ""} · expected ${_nvEsc(h.away)} ${f1(ea)} – ${f1(eh)} ${_nvEsc(h.home)}${own}</div>` : ""}
      </div>
      <button class="nv-x" type="button" data-nv="close" aria-label="Close">✕</button>
    </div>
    ${startedNote}
    <div class="nv-pick">
      <button type="button" class="nv-btn${isTyp ? " on" : ""}" data-nv="sim" data-k="0">Typical game</button>
      <button type="button" class="nv-btn${!isTyp ? " on" : ""}" data-nv="sim" data-k="${isTyp ? 1 : (h.k % ((h.sims || []).length - 1)) + 1}">Another sim ▸</button>
      <span class="nv-which">${which}${against}</span>
    </div>
    <div class="nv-sb">
      <div class="nv-team nv-away"><span class="nv-ab">${_nvEsc(h.away)}</span><span class="nv-pts" id="nvPtsA">0</span></div>
      <div class="nv-mid"><div id="nvQC" class="nv-qc"></div><div id="nvDD" class="nv-dd"></div></div>
      <div class="nv-team nv-home"><span class="nv-pts" id="nvPtsH">0</span><span class="nv-ab">${_nvEsc(h.home)}</span></div>
    </div>
    <div class="nv-fieldwrap">${_nvFieldSvg(teams)}<div id="nvBanner" class="nv-banner hidden"></div></div>
    <div id="nvText" class="nv-text"></div>
    <div class="nv-ctl">
      <button type="button" class="nv-btn" data-nv="prev" aria-label="Previous play">⏮</button>
      <button type="button" class="nv-btn nv-play" data-nv="play" id="nvPlayBtn" aria-label="Play">▶</button>
      <button type="button" class="nv-btn" data-nv="next" aria-label="Next play">⏭</button>
      <button type="button" class="nv-btn" data-nv="speed" id="nvSpeed">1x</button>
      <input type="range" id="nvScrub" class="nv-scrub" min="0" max="${Math.max(0, _NV.ev.length - 1)}" value="0" aria-label="Play">
      <span class="nv-qs">${[1, 2, 3, 4, 5].filter((q) => _NV.ev.some((e) => e.q === q)).map((q) =>
        `<button type="button" class="nv-btn nv-q" data-nv="q" data-q="${q}">${_nvQ(q)}</button>`).join("")}</span>
    </div>
    <div class="nv-tabs">
      ${[["plays", "Plays"], ["box", "Box score"], ["downs", "By down"], ["not", "Not modelled"]].map(([t, l]) =>
        `<button type="button" class="nv-tab${_NV.tab === t ? " on" : ""}" data-nv="tab" data-tab="${t}">${l}</button>`).join("")}
    </div>
    <div id="nvPanel" class="nv-panel"></div>
    <div class="nv-foot">One simulated game, not a prediction of any play. The deep engine simulates the team's plays; who carries, catches, kicks and tackles is drawn by each player's share of Sleeper's week ${_nvEsc(h.week)} projections, and a defender is named only on a tackle that ends a play in bounds, a sack, an interception, a lost fumble or a return touchdown. Run direction, pass side and shotgun follow each team's real mix this season (${_nvEsc((h.flavor || {}).source || "")}). Positions are illustrative. The clock is the engine's at each snap; it does not run inside a play. About one sack in twelve nets yards in this engine: a fitting error reported 2026-10-29 and not yet refitted, marked "artifact" where it happens.${missing}</div>
  </div>`;
}

// ---- the field -------------------------------------------------------------------------
// SVG units are tenths of a yard: x 0-1200 (the home end zone 0-100, the field
// 100-1100, the away end zone 1100-1200), y 0-533. Home attacks to the right.
const _NV_W = 533;
function _nvX(a) { return 100 + 10 * a; }
function _nvFieldSvg(teams) {
  let lines = "";
  for (let a = 0; a <= 100; a += 5) {
    lines += `<line x1="${_nvX(a)}" y1="0" x2="${_nvX(a)}" y2="${_NV_W}" class="${a % 10 === 0 ? "nv-l10" : "nv-l5"}"/>`;
  }
  let nums = "";
  for (let a = 10; a <= 90; a += 10) {
    const n = a <= 50 ? a : 100 - a;
    nums += `<text x="${_nvX(a)}" y="70" class="nv-num">${n}</text><text x="${_nvX(a)}" y="${_NV_W - 45}" class="nv-num">${n}</text>`;
  }
  let dots = "";
  for (let j = 0; j < 11; j++) dots += `<circle id="nvO${j}" r="9" class="nv-dot nv-off" cx="-50" cy="-50"/>`;
  for (let j = 0; j < 11; j++) dots += `<circle id="nvD${j}" r="9" class="nv-dot nv-def" cx="-50" cy="-50"/>`;
  return `<svg class="nv-field" viewBox="0 0 1200 ${_NV_W}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Field">
    <rect x="0" y="0" width="1200" height="${_NV_W}" class="nv-grass"/>
    <rect x="0" y="0" width="100" height="${_NV_W}" class="nv-ez nv-ez-h"/>
    <rect x="1100" y="0" width="100" height="${_NV_W}" class="nv-ez nv-ez-a"/>
    <text x="50" y="${_NV_W / 2}" class="nv-eztx" transform="rotate(-90 50 ${_NV_W / 2})">${_nvEsc(teams[0])}</text>
    <text x="1150" y="${_NV_W / 2}" class="nv-eztx" transform="rotate(90 1150 ${_NV_W / 2})">${_nvEsc(teams[1])}</text>
    ${lines}${nums}
    <line id="nvLOS" x1="-10" y1="0" x2="-10" y2="${_NV_W}" class="nv-los"/>
    <line id="nvFD" x1="-10" y1="0" x2="-10" y2="${_NV_W}" class="nv-fd"/>
    <g id="nvDots">${dots}</g>
    <rect id="nvFlag" width="16" height="12" x="-50" y="-50" class="nv-flag"/>
    <ellipse id="nvBall" rx="9" ry="6" cx="-50" cy="-50" class="nv-ball"/>
    <text id="nvLblC" class="nv-lbl" x="-50" y="-50"></text>
    <text id="nvLblD" class="nv-lbl nv-lbld" x="-50" y="-50"></text>
  </svg>`;
}
// Stock 11 personnel against a 4-2-5 nickel, [lateral yards (the offence's right
// +), yards off the ball]. For looks only (Fable m9: the engine has no personnel).
// Spread wider than real splits so the dots do not merge at this scale.
const _NV_OFF = [[-4, 0.8], [-2, 0.8], [0, 0.8], [2, 0.8], [4, 0.8], [6, 1], [-20, 1], [20, 0.8], [12, 1.2], [0, 2.6], [0, 7]];
const _NV_OFF_GUN = { 9: [0, 5], 10: [-2, 5] };
const _NV_DEF = [[-5, 1.2], [-2, 1.2], [2, 1.2], [5, 1.2], [-4, 5], [4, 5], [-20, 6], [20, 6], [12, 5], [-9, 12], [9, 12]];
const O_TE = 5, O_X = 6, O_Z = 7, O_SLOT = 8, O_QB = 9, O_RB = 10;
const D_DE1 = 0, D_DE2 = 3, D_LB1 = 4, D_LB2 = 5, D_CB1 = 6, D_CB2 = 7, D_NB = 8, D_S1 = 9, D_S2 = 10;

function _nvPt(a, lat, dir) {
  return [_nvX(a), Math.max(12, Math.min(_NV_W - 12, _NV_W / 2 + 10 * lat * dir))];
}
function _nvLerp(p, q, t) { return [p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t]; }
function _nvPath(pts, t) {
  // pts: [[t, [x, y]], ...] sorted by t
  if (t <= pts[0][0]) return pts[0][1];
  for (let j = 1; j < pts.length; j++) {
    if (t <= pts[j][0]) {
      const [t0, p0] = pts[j - 1], [t1, p1] = pts[j];
      return _nvLerp(p0, p1, t1 > t0 ? (t - t0) / (t1 - t0) : 1);
    }
  }
  return pts[pts.length - 1][1];
}
function _nvSet(id, p, show) {
  const n = document.getElementById(id);
  if (!n) return;
  if (show === false || !p) { n.setAttribute("cx", -50); n.setAttribute("cy", -50); return; }
  n.setAttribute("cx", p[0].toFixed(1)); n.setAttribute("cy", p[1].toFixed(1));
}
function _nvLabel(id, p, txt, dy) {
  const n = document.getElementById(id);
  if (!n) return;
  if (!p || !txt) { n.setAttribute("x", -50); n.setAttribute("y", -50); n.textContent = ""; return; }
  n.textContent = txt;
  n.setAttribute("x", p[0].toFixed(1)); n.setAttribute("y", (p[1] + dy).toFixed(1));
}
function _nvLine(id, a) {
  const n = document.getElementById(id);
  if (!n) return;
  const x = a == null ? -10 : _nvX(a);
  n.setAttribute("x1", x); n.setAttribute("x2", x);
}

// The play's plan: where each moving piece starts and ends, as keyframes in t.
function _nvPlan(e) {
  const dir = e.o === 0 ? 1 : -1;
  const x0 = e.x0 != null ? e.x0 : 50;
  const gun = e.f === "g";
  const off = _NV_OFF.map((p, j) => (gun && _NV_OFF_GUN[j]) ? _NV_OFF_GUN[j] : p);
  const oStart = off.map(([lat, dep]) => _nvPt(x0 - dir * dep, lat, dir));
  const dStart = _NV_DEF.map(([lat, dep]) => _nvPt(x0 + dir * dep, lat, dir));
  const plan = { oStart, dStart, moves: {}, ball: null, dots: true, flag: false };
  const players = (_NV.head && _NV.head.players) || [];
  const posOf = (i) => (i != null && players[i]) ? String(players[i].p || "") : "";
  const lr = e.lr || 0;
  const x1 = e.x1 != null ? e.x1 : x0;
  const k = e.k;
  if (k === "run" || (k === "try" && e.x0 != null && Math.abs(e.x0 - (e.o === 0 ? 98 : 2)) < 0.5)) {
    const who = posOf(e.ca) === "QB" ? O_QB : O_RB;
    const s = oStart[who];
    const end = _nvPt(x1 + (e.b === "td" ? dir * 3 : 0), lr * 3, dir);
    const hole = _nvPt(x0 - dir * 0.5, lr * 3, dir);
    plan.moves["O" + who] = [[0, s], [0.3, hole], [1, end]];
    plan.ball = [[0, oStart[O_QB]], [0.15, s], [0.3, hole], [1, end]];
    plan.carrier = { id: "O" + who, at: end };
    if (e.df != null) {
      const d = lr < 0 ? D_LB1 : D_LB2;
      plan.moves["D" + d] = [[0, dStart[d]], [1, [end[0] + dir * 10, end[1] + 4]]];
      plan.def = { at: [end[0] + dir * 10, end[1] + 4] };
    }
  } else if (k === "cmp" || k === "inc" || k === "int") {
    const pos = posOf(e.ca);
    const who = pos === "TE" ? O_TE : pos === "RB" ? O_RB : lr < 0 ? O_X : lr > 0 ? O_Z : O_SLOT;
    const yd = e.yd || 0;
    let air = e.deep ? 18 : 6;
    if (k === "cmp") air = yd <= 0 ? -1 : (e.deep ? Math.min(yd, Math.max(15, Math.round(yd * 0.8))) : Math.min(yd, Math.max(1, Math.round(yd * 0.6))));
    const lane = lr < 0 ? -12 : lr > 0 ? 12 : 0;
    const catchPt = _nvPt(x0 + dir * air, lane, dir);
    const qbBack = _nvPt(x0 - dir * (gun ? 5.5 : 6.5), 0, dir);
    plan.moves["O" + O_QB] = [[0, oStart[O_QB]], [0.2, qbBack]];
    plan.moves["O" + who] = [[0, oStart[who]], [0.55, catchPt]];
    if (k === "cmp") {
      const end = _nvPt(x1 + (e.b === "td" ? dir * 3 : 0), lane, dir);
      plan.moves["O" + who].push([1, end]);
      plan.ball = [[0, oStart[O_QB]], [0.25, qbBack], [0.55, catchPt], [1, end]];
      plan.arc = [0.25, 0.55];
      plan.carrier = { id: "O" + who, at: end };
      if (e.df != null) {
        const d = lr < 0 ? (e.deep ? D_S1 : D_CB1) : lr > 0 ? (e.deep ? D_S2 : D_CB2) : (e.deep ? D_S1 : D_NB);
        const at = [end[0] + dir * 10, end[1] + 4];
        plan.moves["D" + d] = [[0, dStart[d]], [1, at]];
        plan.def = { at };
      }
    } else if (k === "inc") {
      plan.ball = [[0, oStart[O_QB]], [0.25, qbBack], [0.6, catchPt]];
      plan.arc = [0.25, 0.6];
      plan.carrier = { id: "O" + who, at: catchPt };
    } else {
      const d = lr < 0 ? (e.deep ? D_S1 : D_CB1) : lr > 0 ? (e.deep ? D_S2 : D_CB2) : (e.deep ? D_S1 : D_NB);
      const end = _nvPt(x1, lane * 0.5, dir);
      plan.moves["D" + d] = [[0, dStart[d]], [0.6, catchPt], [1, end]];
      plan.ball = [[0, oStart[O_QB]], [0.25, qbBack], [0.6, catchPt], [1, end]];
      plan.arc = [0.25, 0.6];
      plan.carrier = { id: "O" + who, at: catchPt };
      plan.def = { at: end, ballCarrier: true };
    }
  } else if (k === "sack" || k === "kneel") {
    // A sack the engine scores as a GAIN is a sack plus a penalty enforced on
    // the play (its effective yards): drawn as a sack behind the line, then
    // the ball re-spotted -- never the quarterback carried 30 yards downfield.
    const gain = k === "sack" && (e.yd || 0) > 0;
    const end = gain ? _nvPt(x0 - dir * 6, 0, dir) : _nvPt(x1, 0, dir);
    plan.moves["O" + O_QB] = [[0, oStart[O_QB]], [0.7, end]];
    plan.ball = gain ? [[0, oStart[O_QB]], [0.7, end], [0.85, end], [1, _nvPt(x1, 0, dir)]] : [[0, oStart[O_QB]], [0.7, end]];
    plan.flag = gain;
    plan.carrier = { id: "O" + O_QB, at: end };
    if (k === "sack" && e.df != null) {
      const d = (e.df % 2) ? D_DE2 : D_DE1;
      const at = [end[0] + dir * 8, end[1] - 6];
      plan.moves["D" + d] = [[0, dStart[d]], [0.7, at]];
      plan.def = { at };
    }
  } else if (k === "pen") {
    plan.ball = [[0, _nvPt(x0, 0, dir)], [0.8, _nvPt(x0, 0, dir)], [1, _nvPt(x1, 0, dir)]];
    plan.flag = true;
  } else if (k === "punt") {
    const p = _nvPt(x0 - dir * 14, 0, dir);
    plan.moves["O" + O_RB] = [[0, p]];
    plan.ball = [[0, p], [1, _nvPt(x1, 0, dir)]];
    plan.arc = [0, 1];
  } else if (k === "fg" || k === "try") {
    const hold = _nvPt(x0 - dir * 7, 0, dir);
    plan.ball = [[0, hold], [1, _nvPt(x1, 0, dir)]];
    plan.arc = [0, 1];
    if (e.ca != null) plan.carrier = { id: null, at: hold };
  } else if (k === "ko" || k === "fk") {
    const kdir = e.o === 0 ? 1 : -1;
    plan.dots = false;
    plan.ball = [[0, _nvPt(x0, 0, kdir)], [1, _nvPt(x1, 0, kdir)]];
    plan.arc = [0, 1];
    if (e.ca != null) plan.carrier = { id: null, at: _nvPt(x1, 0, kdir) };
  } else {
    plan.dots = false;
  }
  if (e.k !== "int" && e.b === "fum" && e.xi != null) {
    // the fumble: the carrier gets to the spot, the ball comes loose, the
    // recovery is where the defence takes over
    const fs = _nvPt(e.xi, lr * 3, dir), end = _nvPt(x1, lr * 3, dir);
    if (plan.carrier && plan.carrier.id) plan.moves[plan.carrier.id] = [[0, plan.oStart ? oStart[+plan.carrier.id.slice(1)] : fs], [0.6, fs]];
    plan.ball = (plan.ball || [[0, fs]]).filter((kf) => kf[0] < 0.6).concat([[0.6, fs], [1, end]]);
    plan.carrier = plan.carrier ? { id: plan.carrier.id, at: fs } : null;
    plan.def = { at: end, ballCarrier: true };
  }
  return plan;
}

function _nvFrame(plan, t) {
  const e = _NV.ev[_NV.i] || {};
  for (let j = 0; j < 11; j++) {
    const mo = plan.moves["O" + j], md = plan.moves["D" + j];
    _nvSet("nvO" + j, mo ? _nvPath(mo, t) : plan.oStart[j], plan.dots);
    _nvSet("nvD" + j, md ? _nvPath(md, t) : plan.dStart[j], plan.dots);
  }
  let b = plan.ball ? _nvPath(plan.ball, t) : null;
  if (b && plan.arc && t > plan.arc[0] && t < plan.arc[1]) {
    const u = (t - plan.arc[0]) / (plan.arc[1] - plan.arc[0]);
    b = [b[0], b[1] - Math.sin(Math.PI * u) * 45];
  }
  _nvSet("nvBall", b, !!b);
  const fl = document.getElementById("nvFlag");
  if (fl) {
    const on = plan.flag && t >= 0.4 && e.x0 != null;
    fl.setAttribute("x", on ? (_nvX(e.x0) - 8).toFixed(1) : -50);
    fl.setAttribute("y", on ? (_NV_W / 2 + 40) : -50);
  }
  const players = (_NV.head && _NV.head.players) || [];
  const cName = e.ca != null && players[e.ca] ? players[e.ca].n : "";
  const dName = e.df != null && players[e.df] ? players[e.df].n : "";
  let cp = null;
  if (plan.carrier) cp = plan.carrier.id ? _nvPath(plan.moves[plan.carrier.id] || [[0, plan.carrier.at]], t) : plan.carrier.at;
  _nvLabel("nvLblC", t > 0.05 ? cp : null, cName, -16);
  const dp = plan.def ? plan.def.at : null;
  _nvLabel("nvLblD", t > 0.85 ? dp : null, dName, 26);
}

// ---- playback ----------------------------------------------------------------------------
function _nvStop() {
  _NV.playing = false;
  clearTimeout(_NV.timer);
  if (_NV.raf) cancelAnimationFrame(_NV.raf);
  _NV.raf = null;
  const b = document.getElementById("nvPlayBtn");
  if (b) { b.textContent = "▶"; b.setAttribute("aria-label", "Play"); }
}
function _nvDur(e) {
  if (_NV_SNAPS[e.k] || _NV_KICKS[e.k] || e.k === "try") return 1400;
  return 0;
}
function _nvShow(i, animate, done) {
  const ev = _NV.ev;
  if (!ev.length) return;
  i = Math.max(0, Math.min(ev.length - 1, i));
  _NV.i = i;
  const e = ev[i], h = _NV.head;
  const before = i > 0 ? ev[i - 1].s : [0, 0];
  const teams = [h.home, h.away];
  const setScore = (s) => {
    const a = document.getElementById("nvPtsA"), hh = document.getElementById("nvPtsH");
    if (a) a.textContent = s[1];
    if (hh) hh.textContent = s[0];
  };
  setScore(animate ? before : e.s);
  const qc = document.getElementById("nvQC");
  if (qc) qc.textContent = `${_nvQ(e.q)} ${_nvClock(e.c)}`;
  const dd = document.getElementById("nvDD");
  if (dd) {
    dd.innerHTML = e.d ? `<span class="nv-poss">${_nvEsc(teams[e.o])} ball</span> ${_nvOrd(e.d)} & ${e.t >= e.y ? "Goal" : e.t} at ${_nvEsc(_nvSpot(teams, e.o, e.y))}`
      : (e.o != null && (_NV_KICKS[e.k] || e.k === "try") ? `<span class="nv-poss">${_nvEsc(teams[e.o])}</span>` : "");
  }
  for (const [side, cls] of [[1, ".nv-away"], [0, ".nv-home"]]) {
    const n = _NV.el.querySelector(cls);
    if (n) n.classList.toggle("nv-has", e.o === side && !!(e.d || _NV_KICKS[e.k]));
  }
  _nvLine("nvLOS", e.x0 != null && (_NV_SNAPS[e.k] || e.k === "punt" || e.k === "fg") ? e.x0 : null);
  _nvLine("nvFD", e.fx != null && _NV_SNAPS[e.k] ? e.fx : null);
  const txt = document.getElementById("nvText");
  if (txt) txt.innerHTML = `<span class="nv-tq">${_nvQ(e.q)} ${_nvClock(e.c)}</span> ${_nvEsc(e.txt)}${e.art ? _NV_ART : ""}`;
  const sc = document.getElementById("nvScrub");
  if (sc) sc.value = i;
  _nvBanner(null);
  const plan = _nvPlan(e);
  const finish = () => {
    _nvFrame(plan, 1);
    setScore(e.s);
    _nvBanner(e);
    _nvPanel();
    if (done) done();
  };
  const dur = animate ? _nvDur(e) / _NV.speed : 0;
  if (!dur) { finish(); return; }
  if (_NV.raf) cancelAnimationFrame(_NV.raf);
  const t0 = performance.now();
  const tick = (now) => {
    const t = Math.min(1, (now - t0) / dur);
    _nvFrame(plan, t);
    if (t < 1) { _NV.raf = requestAnimationFrame(tick); } else { _NV.raf = null; finish(); }
  };
  _nvFrame(plan, 0);
  _NV.raf = requestAnimationFrame(tick);
}
function _nvBanner(e) {
  const b = document.getElementById("nvBanner");
  if (!b) return;
  let txt = e && e.b ? _NV_BANNER[e.b] : "";
  if (e && e.b === "end") txt = e.q === 2 ? "HALFTIME" : e.q === 4 ? "END OF REGULATION" : e.q >= 5 ? "END OF OVERTIME" : `END OF ${_nvQ(e.q)}`;
  if (!txt) { b.textContent = ""; b.className = "nv-banner hidden"; return; }
  b.textContent = txt;
  b.className = `nv-banner nv-b-${e.b}`;
}
function _nvPlayLoop() {
  if (!_NV.playing) return;
  if (_NV.i >= _NV.ev.length - 1) { _nvStop(); return; }
  _nvShow(_NV.i + 1, true, () => {
    if (!_NV.playing) return;
    const e = _NV.ev[_NV.i];
    const dwell = (e.b ? 1500 : _nvDur(e) ? 700 : 900) / _NV.speed;
    _NV.timer = setTimeout(_nvPlayLoop, dwell);
  });
}
function _nvTogglePlay() {
  if (_NV.playing) { _nvStop(); return; }
  if (_NV.i >= _NV.ev.length - 1) _nvShow(0, false);
  _NV.playing = true;
  const b = document.getElementById("nvPlayBtn");
  if (b) { b.textContent = "⏸"; b.setAttribute("aria-label", "Pause"); }
  _nvPlayLoop();
}

// ---- the panels --------------------------------------------------------------------------
function _nvPanel() {
  const p = document.getElementById("nvPanel");
  if (!p || !_NV.head) return;
  if (_NV.tab === "plays") p.innerHTML = _nvPlays();
  else if (_NV.tab === "box") p.innerHTML = _nvBox();
  else if (_NV.tab === "downs") p.innerHTML = _nvDowns();
  else p.innerHTML = `<ul class="nv-not">${_NV_NOT_MODELLED.map((x) => `<li>${_nvEsc(x)}</li>`).join("")}</ul>
    <div class="nv-small">The engine plays overtime as it is played: both sides get a possession, then sudden death; 10 minutes and a tie in the regular season.</div>`;
}
function _nvPlays() {
  const rows = [];
  for (let j = _NV.i; j >= 0 && rows.length < 400; j--) {
    const e = _NV.ev[j];
    const cls = e.b === "td" || e.b === "fg" || e.b === "sf" ? " nv-score" : e.b === "int" || e.b === "fum" || e.b === "tod" ? " nv-to" : "";
    rows.push(`<div class="nv-row${cls}${j === _NV.i ? " nv-cur" : ""}" data-nv="jump" data-i="${j}"><span class="nv-tq">${_nvQ(e.q)} ${_nvClock(e.c)}</span> ${_nvEsc(e.txt)}${e.art ? _NV_ART : ""} <span class="nv-rs">${_nvEsc(_NV.head.away)} ${e.s[1]}-${e.s[0]} ${_nvEsc(_NV.head.home)}</span></div>`);
  }
  return rows.join("");
}
function _nvTotals() {
  const tot = {};
  for (let j = 0; j <= _NV.i; j++) {
    for (const [pi, st, n] of (_NV.ev[j].cr || [])) {
      const t = tot[pi] || (tot[pi] = {});
      t[st] = (t[st] || 0) + n;
    }
  }
  return tot;
}
function _nvBox() {
  const h = _NV.head, tot = _nvTotals(), players = h.players || [];
  const side = (s) => Object.keys(tot).map(Number).filter((pi) => players[pi] && players[pi].t === s);
  const v = (pi, st) => (tot[pi] || {})[st] || 0;
  const nm = (pi) => `${_nvEsc(players[pi].n)} <span class="nv-pos">${_nvEsc(players[pi].p)}</span>`;
  const table = (title, cols, rows) => rows.length ? `<table class="nv-tbl"><thead><tr><th>${title}</th>${cols.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table>` : "";
  let out = `<div class="nv-small">Running box score to this play. <span class="nv-share">names by share</span> The team's numbers are the engine's; who gets each one is drawn by Sleeper's projected shares.</div>`;
  for (const s of [1, 0]) {
    const ids = side(s);
    const pass = ids.filter((pi) => v(pi, "pa") || v(pi, "sk")).map((pi) => `<tr><td>${nm(pi)}</td><td>${v(pi, "pc")}/${v(pi, "pa")}</td><td>${v(pi, "py")}</td><td>${v(pi, "ptd")}</td><td>${v(pi, "pi")}</td><td>${v(pi, "sk")}</td></tr>`);
    const rush = ids.filter((pi) => v(pi, "ra")).sort((a, b) => v(b, "ry") - v(a, "ry")).map((pi) => `<tr><td>${nm(pi)}</td><td>${v(pi, "ra")}</td><td>${v(pi, "ry")}</td><td>${v(pi, "rtd")}</td><td>${v(pi, "fl")}</td></tr>`);
    const rec = ids.filter((pi) => v(pi, "tg")).sort((a, b) => v(b, "rcy") - v(a, "rcy")).map((pi) => `<tr><td>${nm(pi)}</td><td>${v(pi, "rc")}/${v(pi, "tg")}</td><td>${v(pi, "rcy")}</td><td>${v(pi, "rctd")}</td></tr>`);
    const def = ids.filter((pi) => v(pi, "tk") || v(pi, "sa") || v(pi, "in") || v(pi, "ff") || v(pi, "fr")).sort((a, b) => v(b, "tk") - v(a, "tk")).map((pi) => `<tr><td>${nm(pi)}</td><td>${v(pi, "tk")}</td><td>${v(pi, "sa")}</td><td>${v(pi, "in")}</td><td>${v(pi, "ff")}</td><td>${v(pi, "fr")}</td></tr>`);
    const kick = ids.filter((pi) => v(pi, "fga") || v(pi, "xpa")).map((pi) => `<tr><td>${nm(pi)}</td><td>${v(pi, "fgm")}/${v(pi, "fga")}</td><td>${v(pi, "xpm")}/${v(pi, "xpa")}</td></tr>`);
    const ret = ids.filter((pi) => v(pi, "ktd") || v(pi, "dtd")).map((pi) => `<tr><td>${nm(pi)}</td><td>${v(pi, "ktd") + v(pi, "dtd")}</td></tr>`);
    out += `<div class="nv-boxteam"><div class="nv-boxhdr">${_nvEsc(s ? h.away : h.home)}</div>
      ${table("Passing", ["C/ATT", "YDS", "TD", "INT", "SK"], pass)}
      ${table("Rushing", ["ATT", "YDS", "TD", "FUM"], rush)}
      ${table("Receiving", ["REC/TGT", "YDS", "TD"], rec)}
      ${table(`Defence <span class="nv-share">by share</span>`, ["TKL", "SACK", "INT", "FF", "FR"], def)}
      ${table("Kicking", ["FG", "XP"], kick)}
      ${table("Return TDs", ["TD"], ret)}</div>`;
  }
  if (_NV.i === _NV.ev.length - 1 && _NV.box) {
    const tb = _NV.box;
    const row = (l, f) => `<tr><td>${l}</td><td>${f(tb.away)}</td><td>${f(tb.home)}</td></tr>`;
    const top = (s) => _nvClock(s);
    out += `<table class="nv-tbl nv-teamtbl"><thead><tr><th>Team (the engine's box)</th><th>${_nvEsc(h.away)}</th><th>${_nvEsc(h.home)}</th></tr></thead><tbody>
      ${row("First downs", (b) => b.first)}${row("Plays", (b) => b.plays)}
      ${row("Passing (net)", (b) => b.pass_yds - b.sack_yds)}${row("Rushing", (b) => b.rush_yds)}
      ${row("Third downs", (b) => `${b.third_conv}/${b.third_att}`)}${row("Fourth downs", (b) => `${b.fourth_conv}/${b.fourth_go}`)}
      ${row("Turnovers", (b) => b.int + b.fum_lost)}${row("Penalties", (b) => b.pen)}
      ${row("Time of possession", (b) => top(b.top))}</tbody></table>`;
  }
  return out;
}
function _nvDowns() {
  const h = _NV.head, pd = h.per_down || {};
  const pct = (x) => x == null ? "–" : `${x}%`;
  const one = (name, rows) => `<table class="nv-tbl"><thead><tr><th>${_nvEsc(name)} offence</th><th>Plays a game</th><th>Avg to go</th><th>Typical call</th><th>Run / pass</th><th>Median gain (run / pass)</th><th>Success</th><th>Moves the chains</th><th>Sacked</th><th>Picked</th><th>Punt / FG</th></tr></thead><tbody>${
    (rows || []).map((r) => r.n === 0 ? `<tr><td>${_nvOrd(r.down)}</td><td colspan="10">–</td></tr>` :
      `<tr><td>${_nvOrd(r.down)}</td><td>${r.per_game}</td><td>${r.dist}</td><td><b>${_nvEsc(r.call)}</b></td><td>${pct(r.run_pct)} / ${pct(r.pass_pct)}</td><td>${r.run_med == null ? "–" : r.run_med} / ${r.pass_med == null ? "–" : r.pass_med}</td><td>${pct(r.success)}</td><td>${pct(r.first)}</td><td>${pct(r.sack_pct)}</td><td>${pct(r.int_pct)}</td><td>${r.down === 4 ? `${pct(r.punt_pct)} / ${pct(r.fg_pct)}` : "–"}</td></tr>`).join("")}</tbody></table>`;
  return `<div class="nv-small">Every down of all ${Number(h.n_sims || 0).toLocaleString()} simulated games of this matchup, each offence against the other's defence: the call it makes most, what the plays gain (the median; a pass counts its sacks), how often it succeeds (40% of the distance on 1st down, 60% on 2nd, all of it on 3rd and 4th), moves the chains, is sacked or intercepted. Penalties and kneel-downs are not calls.</div>
    ${one(h.away, pd.away)}${one(h.home, pd.home)}`;
}

// ---- the one listener ----------------------------------------------------------------------
document.addEventListener("click", (ev) => {
  const t = ev.target.closest("[data-nv]");
  if (!t) return;
  const act = t.getAttribute("data-nv");
  if (act === "open") {
    nvOpen(_nvTeamCode(t.getAttribute("data-nv-home")), _nvTeamCode(t.getAttribute("data-nv-away")));
  } else if (act === "close") {
    nvClose();
  } else if (act === "play") {
    _nvTogglePlay();
  } else if (act === "prev") {
    _nvStop(); _nvShow(_NV.i - 1, false);
  } else if (act === "next") {
    _nvStop(); _nvShow(_NV.i + 1, true);
  } else if (act === "speed") {
    _NV.speed = _NV.speed >= 4 ? 1 : _NV.speed * 2;
    t.textContent = `${_NV.speed}x`;
  } else if (act === "q") {
    const q = parseInt(t.getAttribute("data-q"), 10);
    const j = _NV.ev.findIndex((e) => e.q === q);
    if (j >= 0) { _nvStop(); _nvShow(j, false); }
  } else if (act === "tab") {
    _NV.tab = t.getAttribute("data-tab") || "plays";
    _NV.el.querySelectorAll(".nv-tab").forEach((b) => b.classList.toggle("on", b === t));
    _nvPanel();
  } else if (act === "jump") {
    const j = parseInt(t.getAttribute("data-i"), 10);
    if (j >= 0) { _nvStop(); _nvShow(j, false); }
  } else if (act === "sim") {
    const k = parseInt(t.getAttribute("data-k"), 10);
    if (k >= 0) { _NV.k = k; _nvLoad(); }
  }
});
document.addEventListener("input", (ev) => {
  if (ev.target && ev.target.id === "nvScrub") {
    _nvStop();
    _nvShow(parseInt(ev.target.value, 10) || 0, false);
  }
});
document.addEventListener("keydown", (ev) => {
  if (!_NV.el || _NV.el.classList.contains("hidden")) return;
  if (ev.key === "Escape") nvClose();
  else if (ev.key === "ArrowRight") { _nvStop(); _nvShow(_NV.i + 1, true); }
  else if (ev.key === "ArrowLeft") { _nvStop(); _nvShow(_NV.i - 1, false); }
  else if (ev.key === " ") { ev.preventDefault(); _nvTogglePlay(); }
});
