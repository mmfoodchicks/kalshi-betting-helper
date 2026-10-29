// ---- Matchups: offense vs defense (NFL / NBA) ------------------------------
// The owner's ask (2026-10-23): "Don't merely tell me a player is facing the
// 27th-ranked defense. Tell me what that defense struggles to stop, how the
// player generates his production, whether the offensive system is likely to
// exploit that weakness, how frequently similar situations have occurred, and
// how much those factors should actually change our probability estimate."
//
// This tab renders the engines' shared payload (/api/matchups): an
// opportunity scanner over every priced line, a player page (offensive
// profile, opponent profile, the matchup matrix whose rows are the factors the
// model actually applied, every alternate line with its hit rates, the model's
// probability and band, Kalshi's executable price and the fee-adjusted EV), and
// a side-by-side compare. Nothing here computes a probability: every number
// comes from the engine with its evidence attached, and the "kind" of each
// fact (observed / modelled / proxy / assumption) is shown, so a proxy can
// never read as charted data.
//
// No inline event handlers: every control carries data-mx attributes and one
// delegated listener reads them. An id interpolated into onclick="...('x')"
// is decoded by the attribute parser BEFORE the script runs, so escaping it
// does not stop a quote from ending the string (the Fable review's B1 ran an
// alert() through a player id that way, 2026-10-23).
let _mx = { sport: "nfl", date: "", data: null, err: "", view: "scan", player: "", cmp: ["", ""],
  label: "", stat: "", game: "", listed: true, sort: "best", dir: -1, side: "best", open: {}, full: {}, retry: null, tries: 0 };
// The owner's sort menu and side picker (2026-10-23): "Best picks" ranks the
// believable +EV lines by their EV at the CAUTIOUS end of the model's band
// (the EV itself where the engine has no band), thin books and lines the
// record impeaches last; the side picker shows every line's YES or NO side,
// or each line's better side.
const _MX_SORTS = [["best", "Best picks"], ["ev", "Highest EV"], ["p", "Highest probability"],
  ["edge", "Biggest edge vs market"], ["nef", "Most evidence"]];
const _MX_SIDES = [["best", "Best side"], ["yes", "YES"], ["no", "NO"]];
// The slate arrives as an INDEX (every player's lines, nothing else) and each
// game's full players -- profiles, the matrix, comparables -- as its own piece,
// fetched when a player page opens: the whole slate was 18.8 MB of JSON for one
// NBA night (matchups.py).
const _MX_CAP = 300;            // scanner rows drawn at once (the rest are counted, never hidden silently)

const _MX_STAT = {
  pts: "Points", reb: "Rebounds", ast: "Assists", fg3m: "Threes", pra: "Pts+Reb+Ast", pr: "Pts+Reb",
  pa: "Pts+Ast", ra: "Reb+Ast", stl: "Steals", blk: "Blocks", dk_fp: "DK fantasy pts",
  rec: "Receptions", rec_yd: "Rec Yds", rush_yd: "Rush Yds", rush_att: "Carries", pass_yd: "Pass Yds",
  pass_td: "Pass TDs", pass_att: "Pass Att", td: "Anytime TD", tgt: "Targets",
};
// What each label means, said once on the tab. They come from prop_value's
// rules over calibrated inputs, never from a hidden score.
const _MX_LABELS = {
  high_probability: ["High probability", "The model expects this side often. Says nothing about the price."],
  positive_ev: ["+EV", "Positive expected value after Kalshi's fee at the executable ask - if the model's probability is right."],
  positive_ev_robust: ["Robust +EV", "Still positive at the LOW end of the model's probability band."],
  strong_fit: ["Strong fit", "The matchup terms the model kept (what this defense measurably concedes) move this projection 5% or more toward the named side. Says nothing about price."],
  low_evidence: ["Low evidence", "Too little reliable data behind the adjustment for a confident read."],
};
// Qualifiers (prop_value.QUALIFIERS) stand where a +EV label was WITHHELD:
// they explain the price, they are not value judgements, so they are drawn
// muted rather than as a positive chip.
const _MX_QUALS = {
  thin_book: ["thin book", "EV label withheld: the book is one-sided, wider than 20c or untraded -- a listing, not an opinion."],
  stale_quote: ["stale quote", "EV label withheld: the quote is older than the freshness limit, or has no timestamp."],
  // matchups.mark_record: the engine's held-out record says the market's own
  // price forecast this stat better than the model
  market_better: ["market better on record", "On the engine's record against Kalshi's own pre-game prices, the market forecast this stat better than the model: this +EV is the model disagreeing with a better forecaster, not a proven edge."],
};

function _mxEsc(x) { return typeof escapeHtml === "function" ? escapeHtml(x) : String(x == null ? "" : x).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function _mxOk(x) { return x != null && typeof x === "number" && isFinite(x); }
function _mxPct(p, d) { return _mxOk(p) ? `${(100 * p).toFixed(d == null ? 0 : d)}%` : "-"; }
function _mxNum(x, d) { return _mxOk(x) ? Number(x).toFixed(d == null ? 1 : d) : "-"; }
function _mxSigned(x, d) { return _mxOk(x) ? `${x > 0 ? "+" : ""}${Number(x).toFixed(d == null ? 1 : d)}` : "-"; }
// Kalshi quotes and EV arrive in cents (kalshi_nba_props market blocks,
// prop_value.line_value), probabilities and edges as fractions.
// A line's probability band, or "no band" when the engine has none (null p_lo /
// p_hi: nothing fitted carries uncertainty -- the NFL board wherever only
// Sleeper's line is in, review F2); never a zero-width "79%-79%".
function _mxBand(l) { return _mxOk(l.p_lo) && _mxOk(l.p_hi) && l.p_hi > l.p_lo ? `${_mxPct(l.p_lo)}-${_mxPct(l.p_hi)}` : "no band"; }
function _mxCents(c) { return _mxOk(c) ? `${Math.round(c)}¢` : "-"; }
function _mxStat(s) { return Object.prototype.hasOwnProperty.call(_MX_STAT, s) ? _MX_STAT[s] : String(s == null ? "" : s); }
function _mxMt(ts) {
  return _mxOk(ts) ? new Date(ts * 1000).toLocaleString("en-US", { weekday: "short", hour: "numeric", minute: "2-digit", timeZone: "America/Denver" }) + " MT" : "";
}
function _mxKind(k) {
  const c = { observed: "var(--yes)", modelled: "var(--accent,#7aa2ff)", proxy: "#e8b93c", assumption: "var(--no)" }[k] || "var(--muted)";
  return `<span class="small" style="color:${c};border:1px solid ${c};border-radius:4px;padding:0 4px">${_mxEsc(k || "unlabelled")}</span>`;
}
// A label chip names the side it is about: "High probability" beside a 21%
// YES is the NO side's 79% (B9).
// A side is "YES" or "NO" or nothing -- whitelisted, never an engine string
// written into the page (the integration review's F1 ran an onerror through
// best_side / fit_side).
function _mxSide(x) { return x === "yes" ? "YES" : x === "no" ? "NO" : ""; }
// The record behind a "market better" chip, from the payload (matchups.market_record).
function _mxRecordText(stat) {
  const rec = (_mx.data || {}).market_record || {};
  const own = Object.prototype.hasOwnProperty.call(rec, stat) ? rec[stat] : null;
  const r = own || (Object.prototype.hasOwnProperty.call(rec, "*") ? rec["*"] : null);
  if (!r || !_mxOk(r.market_ll) || !_mxOk(r.model_ll)) return "";
  return ` Record (${r.season || "?"}, ${_mxNum(r.n, 0)} ${own ? "of this stat's" : "pooled"} markets${r.basis ? `; ${r.basis}` : ""}): Kalshi's log loss ${_mxNum(r.market_ll, 3)} vs the model's ${_mxNum(r.model_ll, 3)} (lower is better).`;
}
function _mxChips(l, stat) {
  const v = l.value || {};
  return (l.labels || []).map((lab) => {
    if (Object.prototype.hasOwnProperty.call(_MX_QUALS, lab)) {
      const q = _MX_QUALS[lab];
      const tip = q[1] + (lab === "market_better" ? _mxRecordText(stat) : "");
      return `<span class="small" title="${_mxEsc(tip)}" style="cursor:help;color:var(--muted);border:1px dashed var(--muted);border-radius:4px;padding:0 4px">${_mxEsc(q[0])}</span>`;
    }
    const m = Object.prototype.hasOwnProperty.call(_MX_LABELS, lab) ? _MX_LABELS[lab] : null;
    let side = "";
    if (lab === "high_probability" && _mxOk(l.p)) side = l.p >= 0.5 ? "YES" : "NO";
    else if (lab === "positive_ev" || lab === "positive_ev_robust") side = _mxSide(v.best_side);
    else if (lab === "strong_fit") side = _mxSide(l.fit_side);
    const name = m ? m[0] : lab;
    return `<span class="ptchip on" title="${_mxEsc(m ? m[1] : "label from the engine")}" style="cursor:help">${_mxEsc(name)}${side ? ` (${_mxEsc(side)})` : ""}</span>`;
  }).join(" ");
}
// The value block is prop_value.line_value's output (EV in cents per
// contract after the fee at the ask, its interval over the model's band);
// show its better side. No EV on either side -> null, drawn as "-" (B7).
// Does the engine's record say the market forecast this stat better? From the
// line's own qualifier, or (for a side the qualifier was not computed for) the
// slate's market_record -- the same rule matchups.mark_record applies.
function _mxImpeached(stat, labels) {
  if ((labels || []).includes("market_better")) return true;
  const rec = (_mx.data || {}).market_record || {};
  const r = Object.prototype.hasOwnProperty.call(rec, stat) ? rec[stat] : (Object.prototype.hasOwnProperty.call(rec, "*") ? rec["*"] : null);
  return !!(r && _mxOk(r.market_ll) && _mxOk(r.model_ll) && r.market_ll < r.model_ll);
}
// The EV block of one chosen side ("yes" / "no"), or each line's better side ("best").
function _mxSideValue(v, labels, side, stat) {
  if (side !== "yes" && side !== "no") return _mxBest(v, labels, stat);
  if (!v) return null;
  const ev = side === "no" ? v.ev_no : v.ev_yes;
  if (!_mxOk(ev)) return null;
  const iv = (v.ev_interval || {})[side] || [null, null];
  return { side, ev, roi: side === "no" ? v.roi_no : v.roi_yes, lo: iv[0], hi: iv[1], ok: v.believable !== false,
    // the stale_quote label was earned by a +EV side: on the other, negative side it is noise (sort review R1)
    mkt: ev > 0 && _mxImpeached(stat, labels), stale: ev > 0 && (labels || []).includes("stale_quote") };
}
// The probability of the side shown, and its band (NO's band is YES's, flipped).
function _mxSideP(l, side) {
  if (side !== "no") return { p: l.p, lo: l.p_lo, hi: l.p_hi };
  return { p: _mxOk(l.p) ? 1 - l.p : null, lo: _mxOk(l.p_hi) ? 1 - l.p_hi : null, hi: _mxOk(l.p_lo) ? 1 - l.p_lo : null };
}
function _mxBest(v, labels, stat) {
  if (!v) return null;
  const side = v.best_side === "no" || v.best_side === "yes" ? v.best_side
    : ((_mxOk(v.ev_no) && (!_mxOk(v.ev_yes) || v.ev_no > v.ev_yes)) ? "no" : "yes");
  const ev = side === "no" ? v.ev_no : v.ev_yes;
  if (!_mxOk(ev)) return null;
  const iv = (v.ev_interval || {})[side] || [null, null];
  // EV off a book prop_value does not believe (one-sided, wider than 20c,
  // untraded) is arithmetic, not a price signal: it is drawn muted and ranks
  // below every believable line (the Fable re-check of prop_value: "the UI
  // must read `believable` before headlining an EV").
  // a line the engine's record impeaches (market_better) is muted and ranked
  // like a thin book: the price is real, the probability is what lost (F18)
  return { side, ev, roi: side === "no" ? v.roi_no : v.roi_yes, lo: iv[0], hi: iv[1], ok: v.believable !== false,
    mkt: (labels || []).includes("market_better") || (stat !== undefined && ev > 0 && _mxImpeached(stat, labels)),
    stale: (labels || []).includes("stale_quote") };
}
function _mxEvCell(b, bold) {
  if (!b) return "-";
  const strong = bold && b.ok && !b.mkt && !b.stale;
  const txt = `${_mxSide(b.side)} ${strong ? "<b>" : ""}${_mxSigned(b.ev, 1)}¢${strong ? "</b>" : ""}`;
  if (!b.ok) return `<span style="color:var(--muted)" title="The book is one-sided, wider than 20c or untraded: this EV is arithmetic against a listing, not a price signal.">${txt} (thin book)</span>`;
  if (b.stale) return `<span style="color:var(--muted)" title="${_mxEsc(_MX_QUALS.stale_quote[1])}">${txt} (stale quote)</span>`;
  if (b.mkt) return `<span style="color:var(--muted)" title="${_mxEsc(_MX_QUALS.market_better[1])}">${txt} (market better on record)</span>`;
  return txt;
}
// The model against the market's own probability (B4): line_value's `edge` is
// model p minus the market reference (`p_market`, of kind `market_kind`: a
// two-sided mid, or an ask-only upper bound).
function _mxVsMkt(v, side) {
  if (!v || !_mxOk(v.p_market)) return "-";
  const no = side === "no";
  // a one-sided book's p_market is an ask-only UPPER bound on YES; flipped it
  // would be a lower bound on NO with no NO contract behind it -- no edge shown
  if (no && v.market_kind !== "two_sided") return "-";
  const pm = no ? 1 - v.p_market : v.p_market, edge = _mxOk(v.edge) ? (no ? -v.edge : v.edge) : null;
  return `mkt ${_mxPct(pm)} <span class="small" style="color:var(--muted)">${_mxEsc(v.market_kind || "")}${v.fee_assumed ? ", fee assumed" : ""}</span> · ${_mxSigned(_mxOk(edge) ? 100 * edge : null)} pts`;
}
// The side a row is shown for: the chosen one, or the line's better side.
function _mxRowSide(r) { return _mx.side === "yes" || _mx.side === "no" ? _mx.side : (r.b ? r.b.side : "yes"); }

// The sort and side are a per-viewer convenience (localStorage, never required).
function _mxSave() {
  try { localStorage.setItem("mxSortSide", JSON.stringify({ sort: _mx.sort, side: _mx.side })); } catch (e) { /* private mode */ }
}
function _mxRestore() {
  try {
    const o = JSON.parse(localStorage.getItem("mxSortSide") || "{}");
    if (_MX_SORTS.some(([k]) => k === o.sort)) _mx.sort = o.sort;
    if (["best", "yes", "no"].includes(o.side)) _mx.side = o.side;
  } catch (e) { /* private mode or bad JSON: defaults */ }
}
function initMatchups() {
  const root = $("mxRoot");
  if (!root) return;
  if (root.dataset.ready) { mxLoad(); return; }
  root.dataset.ready = "1";
  try { const s = localStorage.getItem("mxSport"); if (s === "nba" || s === "nfl") _mx.sport = s; } catch (e) { /* private mode */ }
  _mxRestore();
  root.innerHTML = `<div class="row-between" style="gap:8px;flex-wrap:wrap">
      <div class="subtabs">
        <button class="subtab" data-mx="sport" data-val="nfl">🏈 NFL</button><button class="subtab" data-mx="sport" data-val="nba">🏀 NBA</button>
      </div>
      <div class="subtabs">
        <button class="subtab" data-mx="view" data-val="scan">📈 Opportunities</button><button class="subtab" data-mx="view" data-val="player">🧩 Player</button><button class="subtab" data-mx="view" data-val="cmp">⚖️ Compare</button><button class="subtab" data-mx="view" data-val="record">📒 Record</button>
      </div>
      <input id="mxDate" type="date" style="width:auto" data-mx="date"/>
    </div>
    <div id="mxStatus" class="small" style="margin-top:6px;color:var(--muted)"></div>
    <div id="mxBody" style="margin-top:8px"><div class="empty">Loading…</div></div>`;
  root.addEventListener("click", _mxClick);
  root.addEventListener("change", _mxChange);
  // a tab coming back from the background re-renders at once (a game that started while it
  // was hidden leaves the list now, not at the next poll) and re-arms the live timer
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible" || !(_mx.data || {}).live_filtered) return;
    // back after more than two minutes: look again (quotes, and games that started meanwhile)
    if (Date.now() - (_mx.loadedAt || 0) > 120000) { _mx.tries = 0; mxLoad(true); } else { mxRender(); _mxLiveTimer(); }
  });
  mxLoad();
}

function _mxClick(e) {
  const el = e.target.closest("[data-mx]");
  if (!el || el.tagName === "SELECT" || el.tagName === "INPUT") return;
  const act = el.dataset.mx, val = el.dataset.val || "";
  if (act === "sport") {
    e.preventDefault();
    _mx.sport = val === "nba" ? "nba" : "nfl"; _mx.player = ""; _mx.cmp = ["", ""]; _mx.tries = 0;
    try { localStorage.setItem("mxSport", _mx.sport); } catch (er) { /* private mode */ }
    mxLoad();
  } else if (act === "view") { _mx.view = val; mxRender(); _mxNeedPieces(); }
  else if (act === "open") { e.preventDefault(); _mx.player = val; _mx.view = "player"; mxRender(); _mxNeedPieces(); }
  else if (act === "sort") { if (_mx.sort === val) _mx.dir = -_mx.dir; else { _mx.sort = val; _mx.dir = -1; } _mxSave(); mxRender(); }
  else if (act === "label") { _mx.label = val; mxRender(); }
  else if (act === "toggle") { _mx.open[val] = !_mx.open[val]; mxRender(); }
}
function _mxChange(e) {
  const el = e.target.closest("[data-mx]");
  if (!el) return;
  const act = el.dataset.mx;
  if (act === "date") { _mx.date = el.value; _mx.tries = 0; mxLoad(); }
  else if (act === "stat") { _mx.stat = el.value; mxRender(); }
  else if (act === "side") { _mx.side = ["yes", "no"].includes(el.value) ? el.value : "best"; _mxSave(); mxRender(); }
  else if (act === "sortsel") { _mx.sort = _MX_SORTS.some(([k]) => k === el.value) ? el.value : "best"; _mx.dir = -1; _mxSave(); mxRender(); }
  else if (act === "game") { _mx.game = el.value; mxRender(); }
  else if (act === "listed") { _mx.listed = !!el.checked; mxRender(); }
  else if (act === "pick") { _mx.player = el.value; mxRender(); _mxNeedPieces(); }
  else if (act === "cmp0" || act === "cmp1") { _mx.cmp[act === "cmp0" ? 0 : 1] = el.value; mxRender(); _mxNeedPieces(); }
}

async function mxLoad(retry) {
  const body = $("mxBody");
  if (!body) return;
  if (_mx.retry) { clearTimeout(_mx.retry); _mx.retry = null; }
  if (!retry) {
    // a new sport or date: nothing of the previous board (its notes included) stays on screen
    _mx.tries = 0;
    if ($("mxStatus")) $("mxStatus").innerHTML = "";
    body.innerHTML = `<div class="empty">Loading the ${_mxEsc(_mx.sport.toUpperCase())} matchups…</div>`;
  }
  _mx.data = null; _mx.err = ""; _mx.full = {};
  const q = `sport=${encodeURIComponent(_mx.sport)}${_mx.date ? `&date=${encodeURIComponent(_mx.date)}` : ""}`;
  let res;
  try {
    res = await fetch(`/api/matchups?${q}`);
  } catch (e) {
    _mx.err = "Could not reach the server.";
    mxRender();
    _mxRetrySoon();
    return;
  }
  // A body that is not JSON, or JSON without a known status, is a bad
  // response -- not an empty board and not an outage (B5, B6).
  let d = null;
  try { d = await res.json(); } catch (e) { d = null; }
  if (!d || typeof d !== "object") _mx.err = `The server sent a response the tab cannot read (HTTP ${res.status}).`;
  else if (d.offline) _mx.err = "Offline - the server could not be reached.";
  else if (d.status === "ok" || d.status === "none") _mx.data = d;
  else _mx.err = `Unexpected response (HTTP ${res.status}${d.error ? `: ${String(d.error)}` : ""}).`;
  // A board being (re)built: look again shortly, a bounded number of times,
  // keeping what is on screen meanwhile.
  if (_mx.data && _mx.data.building && _mx.tries < 8) {
    _mx.tries += 1;
    _mx.retry = setTimeout(() => mxLoad(true), 20000);
  } else if (_mx.data && _mx.data.building) {
    // the server's "this page checks again" is no longer true once the tab stops
    _mx.data.why = "Still building after eight checks (about three minutes) - the tab looks again in two minutes.";
    _mx.data.building = false;
    _mx.data.stopped = true;
  }
  _mx.loadedAt = Date.now();
  // a game filter whose game has left the list would empty the table under "every game"
  if (_mx.game && !(((_mx.data || {}).games) || []).some((g) => String(g.game_id) === _mx.game)) _mx.game = "";
  mxRender();
  if (_mx.view !== "scan") _mxNeedPieces();
  // a live board arms the live timer; a failed or unreadable answer looks again in two minutes
  // (a deploy swap or a network blip must not end the polling), a building one has its own retry
  if (_mx.data && _mx.data.status === "ok") _mxLiveTimer();
  else if (!_mx.retry) _mxRetrySoon();
}
function _mxRetrySoon() {
  if (_mx.live) { clearTimeout(_mx.live); _mx.live = null; }
  _mx.live = setTimeout(() => {
    if (document.visibilityState === "hidden") { _mxRetrySoon(); return; }
    _mx.tries = 0;
    mxLoad(true);
  }, 120000);
}
// While today's (or tomorrow's) board is open, look again when the next game starts --
// its players leave the list -- and just after midnight Mountain time, when the next
// day's board takes over (the owner: the next game shows "starting at midnight", never
// mixed into the pool of unplayed players); at least every fifteen minutes for fresh quotes.
function _mxLiveTimer() {
  if (_mx.live) { clearTimeout(_mx.live); _mx.live = null; }
  const d = _mx.data || {};
  if (!d.live_filtered || d.status !== "ok") return;
  const now = Date.now() / 1000;
  const started = _mxStarted(d);
  // a placeholder (a failed or empty build) promises a retry: it is polled like a live board
  const left = d.placeholder || (d.players || []).some((p) => !started.has(String(p.game_id)));
  const next = (d.games || []).map((g) => g.start_ts).concat([d.next_day_ts])
    .filter((t) => _mxOk(t) && t > now).sort((a, b) => a - b)[0];
  // with nobody left to show, only midnight brings anything: no ten-minute rebuilds of an empty list
  // at most every 15 minutes, the boards' TTL: a shorter poll would only force rebuilds
  const wait = left ? Math.max(30, Math.min(900, _mxOk(next) ? next - now + 5 : 900))
    : Math.max(30, _mxOk(d.next_day_ts) ? d.next_day_ts - now + 5 : 900);
  _mx.live = setTimeout(() => {
    if (document.visibilityState === "hidden") { _mxLiveTimer(); return; }
    // past midnight a date the owner picked as "today" follows the day: asking for it again
    // would serve the finished board, every played player back on it
    if (_mx.date && _mx.date === d.date && _mxOk(d.next_day_ts) && Date.now() / 1000 >= d.next_day_ts) {
      _mx.date = "";
      if ($("mxDate")) $("mxDate").value = "";
    }
    _mx.tries = 0;      // a poll is a fresh look: the building retries count from it, or midnight's build stalls
    mxLoad(true);
  }, wait * 1000);
}

// The full players of the games the open view needs (the player page, the
// two compared players). Fetched once per game per load.
function _mxGameOf(id) {
  const p = ((_mx.data || {}).players || []).find((x) => String(x.player_id) === String(id));
  return p ? String(p.game_id) : "";
}
function _mxFull(id) {
  const g = _mxGameOf(id), piece = _mx.full[g];
  if (!piece || !piece.players) return null;
  return piece.players.find((x) => String(x.player_id) === String(id)) || null;
}
async function _mxNeedPieces() {
  const ids = _mx.view === "player" ? [_mx.player] : _mx.view === "cmp" ? _mx.cmp : [];
  const games = [...new Set(ids.filter(Boolean).map(_mxGameOf).filter(Boolean))].filter((g) => !_mx.full[g]);
  for (const g of games) {
    _mx.full[g] = { loading: true };
    const sport = _mx.sport, date = (_mx.data || {}).date || _mx.date;
    let d = null;
    try {
      const r = await fetch(`/api/matchups?sport=${encodeURIComponent(sport)}&date=${encodeURIComponent(date)}&game=${encodeURIComponent(g)}`);
      d = await r.json();
    } catch (e) { d = null; }
    if (sport !== _mx.sport) return;             // the sport changed while this was in flight
    _mx.full[g] = d && d.status === "ok" ? d : { error: (d && d.why) || "Could not load this game's detail." };
    mxRender();
  }
}

function mxRender() {
  document.querySelectorAll('#mxRoot [data-mx="sport"]').forEach((b) => b.classList.toggle("active", b.dataset.val === _mx.sport));
  document.querySelectorAll('#mxRoot [data-mx="view"]').forEach((b) => b.classList.toggle("active", b.dataset.val === _mx.view));
  const st = $("mxStatus"), body = $("mxBody");
  if (!st || !body) return;
  if (_mx.err) { st.innerHTML = ""; body.innerHTML = `<div class="empty">${_mxEsc(_mx.err)}</div>`; return; }
  const d = _mx.data || {};
  // with no date picked the picker follows the board's day (it turns at midnight)
  if (d.date && $("mxDate") && (!_mx.date || !$("mxDate").value)) $("mxDate").value = d.date;
  if (d.status !== "ok") {
    st.innerHTML = "";
    body.innerHTML = `<div class="empty">${_mxEsc(d.why || "No matchup board for this date yet.")}</div>`;
    return;
  }
  const prov = (d.provenance || []).map((p) => `${_mxEsc(p.source)}${p.covers ? ` (${_mxEsc(p.covers)})` : ""}`).join(" · ");
  // a placeholder (a failed or empty build) is published backdated so it can never
  // out-freshen a full board; its file age is not its age, so none is printed
  const age = _mxOk(d.age_s) && !d.placeholder ? ` (${Math.round(d.age_s / 60)} min ago${d.building ? ", refreshing" : d.stopped ? ", refresh still running - reload to check" : ""})` : "";
  // Slate-level notes carry the engine's own record against the market (e.g.
  // the NBA held-out test: Kalshi's mid beat the model, and betting its edges
  // lost) -- said on every slate, above everything else.
  const notes = (d.notes || []).map((n) => `<div style="margin:4px 0;padding:4px 8px;border-left:3px solid #e8b93c;color:var(--text,inherit)">${_mxEsc(n)}</div>`).join("");
  const slate = d.slate ? ` · ${_mxEsc(d.slate)}` : "";
  st.innerHTML = `${notes}Built ${_mxEsc(_mxMt(d.built_ts))}${age}${slate} · data: ${_mxEsc(d.as_of || "?")} · ${(d.players || []).length} players · sources: ${prov || "-"}
    <details><summary>What the labels mean</summary>${Object.values(_MX_LABELS).concat(Object.values(_MX_QUALS)).map(([n, w]) => `<div><b>${_mxEsc(n)}</b>: ${_mxEsc(w)}</div>`).join("")}</details>`;
  if (_mx.view === "player") body.innerHTML = mxPlayerView();
  else if (_mx.view === "cmp") body.innerHTML = mxCompareView();
  else if (_mx.view === "record") body.innerHTML = mxRecordView();
  else body.innerHTML = mxScanView(d);
}

// ---- Record: the tab graded against Kalshi's own settlement (mx_record) ---------
// Every priced line's last pre-game figure is kept and graded when Kalshi settles it;
// a player who never played is a void (settled at the pre-game price, never scored).
// Each stat earns its own tuning only out of sample, and Kalshi's side of the ledger
// decides the "market better" mute once a stat's record is decided.
const _mxRec = {};
async function _mxRecLoad(sport) {
  const hit = _mxRec[sport];
  if (hit && (hit.loading || Date.now() - hit.at < 300000)) return;
  _mxRec[sport] = { loading: true, at: Date.now(), data: hit && hit.data };
  try {
    const r = await fetch(`/api/matchups/record?sport=${encodeURIComponent(sport)}`);
    const d = await r.json();
    _mxRec[sport] = { at: Date.now(), data: r.ok ? d : null, err: r.ok ? null : (d.error || `HTTP ${r.status}`) };
  } catch (e) {
    _mxRec[sport] = { at: Date.now(), data: null, err: "the record could not be loaded" };
  }
  if (_mx.view === "record" && _mx.sport === sport) mxRender();
}
function _mxRecVerdict(v) {
  if (!v || !v.n) return "no believable books graded yet";
  // bold only when DECIDED -- the same floors and 2-SE rule the "market better" mute reads (mx_record._decide)
  const call = v.decided === "market_better" ? "<b>Kalshi better</b>"
    : v.decided === "engine_better" ? "<b>engine better</b>" : "not decided yet";
  return `${call}: engine ${_mxNum(v.ll_eng, 3)} vs Kalshi ${_mxNum(v.ll_mkt, 3)} (${_mxSigned(v.diff, 3)}${_mxOk(v.se) ? ` ± ${_mxNum(v.se, 3)}` : ""}, ${v.n} lines, ${v.games} games, ${v.days != null ? v.days : "-"} dates)`;
}
function _mxRecPicks(p) {
  if (!p || !p.n) return "none yet";
  return `${p.n} · won ${_mxPct((p.won || 0) / p.n)} · ROI ${_mxOk(p.roi) ? `${_mxSigned(100 * p.roi, 1)}%` : "-"}${_mxOk(p.se) ? ` ± ${_mxNum(100 * p.se, 1)}` : ""}`;
}
function mxRecordView() {
  const sport = _mx.sport;
  const hit = _mxRec[sport];
  if (!hit || (!hit.data && !hit.err)) { _mxRecLoad(sport); return `<div class="empty">Loading the record…</div>`; }
  _mxRecLoad(sport);
  if (!hit.data) return `<div class="empty">${_mxEsc(hit.err || "No record yet.")}</div>`;
  const d = hit.data, stats = d.stats || {}, tun = d.tuning || {};
  const names = Object.keys(stats).filter((s) => s !== "*").sort();
  if (!names.length) return `<div class="empty">Nothing graded yet. Every priced line is kept from now on and graded when Kalshi settles it; the record fills in after the first games finish.</div>`;
  const row = (s, blk, label) => {
    const c = blk.close || {}, t = tun[s] || {};
    const small = ((c.n_eff || 0) < 300 || (c.days || 0) < 10) ? ` <span title="${_mxEsc((d.definitions || {}).small_sample || "")}" style="color:#e0a656">small sample</span>` : "";
    const tt = t.family === "platt" ? `tuned (+${_mxNum(t.gain, 4)} ± ${_mxNum(t.se, 4)} nats a line out of sample)` : (t.why ? `engine as is: ${_mxEsc(t.why)}` : "engine as is");
    const hp = c.picks_hp || {};
    return `<tr><td><b>${_mxEsc(label)}</b>${small}</td><td>${blk.graded || 0}${blk.voids ? ` <span style="color:var(--muted)">(+${blk.voids} void)</span>` : ""}</td>
      <td>${_mxRecVerdict(c.vs_market)}</td>
      <td>${_mxRecPicks((c.picks_ev || {}).shown)}</td><td>${_mxRecPicks((c.picks_ev || {}).muted)}</td>
      <td>${hp.n ? `${hp.n} · hit ${_mxPct(hp.hit)} vs ${_mxPct(hp.pred)} said` : "none yet"}</td><td>${tt}</td></tr>`;
  };
  const cal = (s) => (stats[s].calibration || []).map((b) => `<span style="margin-right:10px">${_mxPct(b.p)} said → ${_mxPct(b.hit)} hit (${b.n})</span>`).join("");
  return `<div class="small" style="color:var(--muted);margin-bottom:6px">Every priced line's last pre-game figure (within 2 h of the start), graded when Kalshi settles it. Players who never played settle at the pre-game price and are counted as voids, never scored. Log loss: lower is better, on the same lines for the engine and Kalshi's de-vigged mid. Picks: one contract on the side and at the ask the tab showed, fee in; "muted" picks are +EV lines in a stat where Kalshi was the better forecaster.</div>
    <div style="overflow-x:auto"><table class="tbl"><thead><tr><th>Stat</th><th>Graded</th><th>Engine vs Kalshi</th><th>+EV picks</th><th>+EV picks (muted)</th><th>High-probability picks</th><th>Tuning</th></tr></thead>
    <tbody>${names.map((s) => row(s, stats[s], _mxStat(s))).join("")}${stats["*"] ? row("*", stats["*"], "All stats") : ""}</tbody></table></div>
    <details style="margin-top:6px"><summary>Calibration of the engine's own figure, before any tuning (what it said, what happened)</summary>${names.map((s) => `<div class="small"><b>${_mxEsc(_mxStat(s))}</b>: ${cal(s) || "-"}</div>`).join("")}</details>`;
}

// ---- Opportunities: every line, ranked ---------------------------------------
// The games already under way on this page's clock: a board the server filtered when it
// answered keeps dropping players as their games start while the tab stays open.
function _mxStarted(d) {
  const now = Date.now() / 1000;
  return new Set(d.live_filtered ? (d.games || []).filter((g) => _mxOk(g.start_ts) && g.start_ts <= now).map((g) => String(g.game_id)) : []);
}
function _mxRows(d) {
  const rows = [];
  _mx.hidden = 0;
  const started = _mxStarted(d);
  for (const p of d.players || []) {
    if (started.has(String(p.game_id))) continue;
    for (const m of p.markets || []) {
      for (const l of m.lines || []) {
        if (_mx.listed && !l.market) { _mx.hidden += 1; continue; }
        rows.push({ p, m, l, b: _mxSideValue(l.value, l.labels, _mx.side, m.stat) });
      }
    }
  }
  return rows;
}
function _mxKey(v) { return _mxOk(v) ? v : -1e12; }     // a missing value sorts last, never NaN (B10)
function mxScanView(d) {
  let rows = _mxRows(d);
  const stats = [...new Set(rows.map((r) => r.m.stat))];
  const started = _mxStarted(d);
  const games = (d.games || []).filter((g) => !started.has(String(g.game_id))).map((g) => [g.game_id, `${g.away} @ ${g.home}`]);
  if (_mx.label) {
    // the +EV chips name a side: with YES or NO chosen, a line qualifies only
    // when THAT side's EV is positive
    const evLab = _mx.label === "positive_ev" || _mx.label === "positive_ev_robust";
    rows = rows.filter((r) => (r.l.labels || []).includes(_mx.label)
      && (!evLab || _mx.side === "best" || (r.b && r.b.ev > 0)));
  }
  if (_mx.stat) rows = rows.filter((r) => String(r.m.stat) === _mx.stat);
  if (_mx.game) rows = rows.filter((r) => String(r.p.game_id) === _mx.game);
  const clean = (r) => r.b && r.b.ok && !r.b.mkt && !r.b.stale;
  // below the clean +EV lines: the muted +EV lines (thin, stale, impeached),
  // then every negative-EV line, then lines with no EV at all
  const demoted = (r, v) => (!r.b ? -1e12 : r.b.ev > 0 ? -1e11 : -2e11) + (_mxOk(v) ? v / 1e3 : -1e8);
  const sideEdge = (r) => {
    const v = r.l.value || {}, e = v.edge, no = _mxRowSide(r) === "no";
    if (!_mxOk(e) || (no && v.market_kind !== "two_sided")) return null;
    return no ? -e : e;
  };
  const key = {
    // a believable, unimpeached +EV line ranks by its cautious EV (the band's
    // unfavourable end; the EV itself without a band); everything else after
    best: (r) => {
      const c = r.b ? (_mxOk(r.b.lo) ? Math.min(r.b.lo, r.b.ev) : r.b.ev) : null;
      // the demoted tiers keep the same cautious order among themselves
      return clean(r) && r.b.ev > 0 ? _mxKey(c) : demoted(r, c);
    },
    ev: (r) => (clean(r) ? _mxKey(r.b.ev) : demoted(r, r.b ? r.b.ev : null)),
    p: (r) => _mxKey(_mxSideP(r.l, _mxRowSide(r)).p),
    edge: (r) => _mxKey(sideEdge(r)), nef: (r) => _mxKey((r.l.hit_similar_def || {}).n_eff),
  }[_mx.sort] || (() => 0);
  rows.sort((a, b) => _mx.dir * (key(a) - key(b)));
  const th = (k, t) => `<th style="cursor:pointer" data-mx="sort" data-val="${k}">${t}${_mx.sort === k ? (_mx.dir < 0 ? " ▾" : " ▴") : ""}</th>`;
  const chip = (v, t) => `<span class="ptchip${_mx.label === v ? " on" : ""}" data-mx="label" data-val="${_mxEsc(v)}" style="cursor:pointer">${_mxEsc(t)}</span>`;
  const body = rows.slice(0, _MX_CAP).map((r) => {
    const mk = r.l.market || {}, b = r.b;
    return `<tr style="border-top:1px solid var(--line,#333)">
      <td style="text-align:left"><a href="#" data-mx="open" data-val="${_mxEsc(r.p.player_id)}">${_mxEsc(r.p.name)}</a> <span class="small" style="color:var(--muted)">${_mxEsc(r.p.pos || "")} ${_mxEsc(r.p.team)} v ${_mxEsc(r.p.opp)}</span></td>
      <td>${_mxEsc(_mxStat(r.m.stat))} ${_mxEsc(r.l.k)}+</td>
      <td>${(() => { const sp = _mxSideP(r.l, _mxRowSide(r)); return `<span class="small" style="color:var(--muted)">${_mxSide(_mxRowSide(r))}</span> <b>${_mxPct(sp.p)}</b> <span class="small" style="color:var(--muted)">${_mxBand({ p_lo: sp.lo, p_hi: sp.hi })}</span>`; })()}</td>
      <td>${r.l.market ? `${_mxCents(mk.yes_ask)} / ${_mxCents(mk.no_ask)}` : "-"}</td>
      <td>${_mxVsMkt(r.l.value, _mxRowSide(r))}</td>
      <td>${b ? `${_mxEvCell(b, true)} <span class="small" style="color:var(--muted)">${_mxSigned(b.lo, 1)} to ${_mxSigned(b.hi, 1)}</span>` : "-"}</td>
      <td>${_mxNum((r.l.hit_similar_def || {}).n_eff, 1)}</td>
      <td style="text-align:left">${_mxChips(r.l, r.m.stat)}</td></tr>`;
  }).join("");
  const more = rows.length > _MX_CAP ? `<div class="small" style="color:var(--muted)">Showing the first ${_MX_CAP} of ${rows.length} lines - filter by stat or game to see the rest.</div>` : "";
  return `<div class="small" style="margin-bottom:6px">Show: ${chip("", "all")} ${Object.entries(_MX_LABELS).map(([k, [n]]) => chip(k, n)).join(" ")}
      · <select data-mx="stat" style="width:auto;padding:2px 4px"><option value="">every stat</option>${stats.map((s) => `<option value="${_mxEsc(s)}"${_mx.stat === String(s) ? " selected" : ""}>${_mxEsc(_mxStat(s))}</option>`).join("")}</select>
      <select data-mx="game" style="width:auto;padding:2px 4px"><option value="">every game</option>${games.map(([id, t]) => `<option value="${_mxEsc(id)}"${_mx.game === String(id) ? " selected" : ""}>${_mxEsc(t)}</option>`).join("")}</select>
      <label style="display:inline-flex;align-items:center;gap:4px;white-space:nowrap"><input type="checkbox" data-mx="listed" ${_mx.listed ? "checked" : ""} style="width:auto;margin:0"/> only lines Kalshi lists</label></div>
    <div class="small" style="margin-bottom:6px">Side: <select data-mx="side" style="width:auto;padding:2px 4px">${_MX_SIDES.map(([k, t]) => `<option value="${k}"${_mx.side === k ? " selected" : ""}>${t}</option>`).join("")}</select>
      · Sort: <select data-mx="sortsel" style="width:auto;padding:2px 4px">${_MX_SORTS.map(([k, t]) => `<option value="${k}"${_mx.sort === k ? " selected" : ""}>${t}</option>`).join("")}</select>
      <span style="color:var(--muted)">${_mx.sort === "best" ? "Best picks: believable +EV lines, ranked by their EV at the cautious end of the model's band; thin books and lines the market beat on record go last." : ""}</span></div>
    <div class="small" style="color:var(--muted);margin-bottom:4px">"vs market" is the model's probability minus the market's own (the two-sided mid, or the ask when only one side is quoted). EV is cents per contract after Kalshi's fee at the ask, for the side shown; the range is the EV at the ends of the model's probability band. "Similar n" is the effective number of past games against comparable defenses.</div>
    <div class="scroller" style="overflow-x:auto"><table class="small" style="border-collapse:collapse;width:100%;min-width:720px;text-align:right">
      <thead><tr><th style="text-align:left;min-width:150px">Player</th><th>Line</th>${th("p", "Model (side)")}<th>YES / NO ask</th>${th("edge", "vs market")}${th("ev", "EV")}${th("nef", "Similar n")}<th style="text-align:left">Labels</th></tr></thead>
      <tbody>${body || `<tr><td colspan="8" class="empty">No lines match.${_mx.hidden ? ` ${_mx.hidden} line${_mx.hidden === 1 ? " is" : "s are"} hidden by "only lines Kalshi lists" - Kalshi lists none of them on this board.` : ""}</td></tr>`}</tbody></table></div>${more}`;
}

// ---- Player: profile, opponent, the matrix, every line ------------------------
// A fact's unit comes from the engine ("share" prints as a percent, anything
// else as a number with its unit): guessing from the size of the number printed
// 0.12 assists a minute as "12.0%" (B3).
function _mxFactVal(val) {
  const v = val.value;
  if (!_mxOk(v)) return _mxEsc(v == null ? "-" : v);
  if (val.unit === "share" || val.unit === "pct") return _mxPct(v, 1);
  return `${_mxNum(v, Number.isInteger(v) ? 0 : (Math.abs(v) >= 100 ? 1 : 2))}${val.unit ? ` <span class="small" style="color:var(--muted)">${_mxEsc(val.unit)}</span>` : ""}`;
}
function _mxFacts(obj) {
  const rows = Object.entries(obj || {}).map(([k, v]) => {
    const val = v && typeof v === "object" && "value" in v ? v : { value: v };
    const basis = val.basis ? `<div class="small" style="color:var(--faint,var(--muted));font-size:11px">${_mxEsc(String(val.basis).slice(0, 140))}</div>` : "";
    return `<tr title="${_mxEsc(val.basis || "")}"><td style="text-align:left;color:var(--muted)">${_mxEsc(k.replace(/_/g, " "))}${basis}</td><td>${_mxFactVal(val)}${val.rank != null ? ` <span class="small" style="color:var(--muted)">(#${_mxEsc(val.rank)})</span>` : ""}</td><td>${_mxOk(val.n) ? `<span class="small" style="color:var(--muted)">n ${_mxNum(val.n, 0)}</span>` : ""}</td><td>${_mxKind(val.kind)}</td></tr>`;
  }).join("");
  return `<table class="small" style="border-collapse:collapse;width:100%;text-align:right">${rows || `<tr><td class="empty">-</td></tr>`}</table>`;
}
function _mxDetail(x) {
  // A factor's supporting data, whatever its shape: a list of rows becomes a
  // table, an object a key/value list.
  if (x == null) return "";
  if (Array.isArray(x)) {
    if (!x.length) return "";
    if (typeof x[0] !== "object") return _mxEsc(x.join(", "));
    const keys = [...new Set(x.flatMap((r) => Object.keys(r || {})))];
    const cell = "padding:2px 8px";
    return `<div class="scroller" style="overflow-x:auto"><table class="small" style="border-collapse:collapse;text-align:right"><thead><tr>${keys.map((k) => `<th style="${cell}">${_mxEsc(k.replace(/_/g, " "))}</th>`).join("")}</tr></thead><tbody>${x.map((r) => `<tr>${keys.map((k) => `<td style="${cell}">${r && typeof r[k] === "object" && r[k] != null ? _mxEsc(JSON.stringify(r[k])) : _mxEsc(r ? r[k] : "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }
  if (typeof x === "object") return `<div style="margin-left:8px">${Object.entries(x).map(([k, v]) => `<div><span style="color:var(--muted)">${_mxEsc(k.replace(/_/g, " "))}:</span> ${v && typeof v === "object" ? _mxDetail(v) : _mxEsc(v)}</div>`).join("")}</div>`;
  return _mxEsc(x);
}
// A factor the model measured but holds at weight 0 (its rung failed the
// held-out test) is folded away by default: it explains what was tried, it
// moves nothing.
function _mxZero(f) { const w = (f.effect || {}).weight; return typeof w === "string" && w.startsWith("0"); }
function _mxMatrix(p, idx) {
  const tone = { favorable: "var(--yes)", unfavorable: "var(--no)", neutral: "var(--muted)" };
  const zkey = `z:${idx}:${p.player_id}`, showZero = _mx.open[zkey];
  const all = p.factors || [], nZero = all.filter(_mxZero).length;
  const rows = all.map((f, i) => [f, i]).filter(([f]) => showZero || !_mxZero(f)).map(([f, i]) => {
    const key = `${idx}:${i}:${p.player_id}`, open = _mx.open[key];
    const eff = f.effect || {}, ev = f.evidence || {};
    return `<tr style="border-top:1px solid var(--line,#333);cursor:pointer" data-mx="toggle" data-val="${_mxEsc(key)}">
        <td style="text-align:left"><b>${_mxEsc(f.factor)}</b>${_mxZero(f) ? ` <span class="small" style="color:var(--muted)">weight 0</span>` : ""}</td><td style="text-align:left">${_mxEsc(f.player || "-")}</td>
        <td style="text-align:left">${_mxEsc(f.opponent)}</td>
        <td style="color:${Object.prototype.hasOwnProperty.call(tone, f.assessment) ? tone[f.assessment] : "inherit"}">${_mxEsc(f.assessment || "")}</td>
        <td>${_mxZero(f) ? `<span class="small" style="color:var(--muted)">not applied</span>` : eff.stat ? `${_mxEsc(_mxStat(eff.stat))} ${_mxSigned(eff.delta_mean, 1)}` : "-"}</td>
        <td>${_mxKind(ev.kind)} <span class="small" style="color:var(--muted)">${_mxOk(ev.n_eff) ? `n_eff ${_mxNum(ev.n_eff, 0)}` : ""}${_mxOk(ev.reliability) ? ` · rel ${_mxNum(ev.reliability, 2)}` : ""}</span></td></tr>
      ${open ? `<tr><td colspan="6" style="text-align:left;background:var(--card2,rgba(127,127,127,.06))">${_mxDetail(f.detail) || "<i>no supporting table</i>"}</td></tr>` : ""}`;
  }).join("");
  const zbtn = nZero ? `<div class="small" style="margin-top:4px"><a href="#" data-mx="toggle" data-val="${_mxEsc(zkey)}">${showZero ? "Hide" : "Show"} the ${nZero} factor${nZero === 1 ? "" : "s"} measured but held at weight 0 (failed the held-out test)</a></div>` : "";
  return `<div class="scroller" style="overflow-x:auto"><table class="small" style="border-collapse:collapse;width:100%;min-width:560px"><thead><tr><th style="text-align:left">Matchup factor</th><th style="text-align:left">Player profile</th><th style="text-align:left">Opponent profile</th><th>Assessment</th><th>Effect on projection</th><th>Evidence</th></tr></thead><tbody>${rows || `<tr><td colspan="6" class="empty">The model applied no matchup factor to this player.</td></tr>`}</tbody></table></div>${zbtn}`;
}
function _mxHit(h) {
  if (!h) return "-";
  if (h.n_eff != null) return `${_mxPct(_mxOk(h.n_eff) && h.n_eff > 0 ? h.hits_w / h.n_eff : null)} <span class="small" style="color:var(--muted)">n_eff ${_mxNum(h.n_eff, 1)}</span>`;
  return `${_mxPct(_mxOk(h.n) && h.n > 0 ? h.hits / h.n : null)} <span class="small" style="color:var(--muted)">${_mxEsc(h.hits)}/${_mxEsc(h.n)}</span>`;
}
function _mxMarkets(p, compact) {
  return (p.markets || []).map((m) => {
    const rows = (m.lines || []).map((l) => {
      const mk = l.market || {}, b = _mxBest(l.value, l.labels, m.stat);
      const evCell = _mxEvCell(b, true);
      if (compact) {
        return `<tr style="border-top:1px solid var(--line,#333)"><td>${_mxEsc(l.k)}+</td><td><b>${_mxPct(l.p)}</b> <span class="small" style="color:var(--muted)">${_mxBand(l)}</span></td>
          <td>${l.market ? `${_mxCents(mk.yes_ask)} / ${_mxCents(mk.no_ask)}` : "-"}</td><td>${evCell}</td><td style="text-align:left">${_mxChips(l, m.stat)}</td></tr>`;
      }
      return `<tr style="border-top:1px solid var(--line,#333)"><td>${_mxEsc(l.k)}+</td><td>${_mxHit(l.hit_all)}</td><td>${_mxHit(l.hit_recent)}</td><td>${_mxHit(l.hit_similar_def)}</td>
        <td><b>${_mxPct(l.p)}</b> <span class="small" style="color:var(--muted)">${_mxBand(l)}</span></td>
        <td>${l.market ? `${_mxCents(mk.yes_bid)}/${_mxCents(mk.yes_ask)} · NO ${_mxCents(mk.no_ask)} <span class="small" style="color:var(--muted)">${_mxEsc(_mxMt(mk.ts))}</span>` : "-"}</td>
        <td>${_mxVsMkt(l.value)}</td>
        <td>${b ? `${evCell} <span class="small" style="color:var(--muted)">(${_mxSigned(b.lo, 1)} to ${_mxSigned(b.hi, 1)}) ROI ${_mxPct(b.roi, 1)}</span>` : "-"}</td>
        <td style="text-align:left">${_mxChips(l, m.stat)}</td></tr>`;
    }).join("");
    // a projection change is only shown when both ends of it exist (B8)
    const delta = _mxOk(m.adjusted_mean) && _mxOk(m.baseline_mean) ? ` (${_mxSigned(m.adjusted_mean - m.baseline_mean)})` : "";
    const head = compact
      ? `<th>Line</th><th>Model (band)</th><th>YES / NO ask</th><th>Best EV</th><th style="text-align:left">Labels</th>`
      : `<th>Line</th><th>Hit, all</th><th>Hit, recent</th><th>Hit, similar defenses</th><th>Model (band)</th><th>Kalshi YES bid/ask · NO ask</th><th>vs market</th><th>Best EV / contract</th><th style="text-align:left">Labels</th>`;
    const src = m.baseline_source ? ` (${_mxEsc(m.baseline_source)})` : "";
    const fam = (m.dist || {}).family ? ` · ${_mxEsc(m.dist.family)}` : "";
    return `<div style="margin-top:10px"><b>${_mxEsc(_mxStat(m.stat))}</b> <span class="small" style="color:var(--muted)">baseline ${_mxNum(m.baseline_mean)}${src} → adjusted <b>${_mxNum(m.adjusted_mean)}</b>${delta}${fam}</span>
      <div class="scroller" style="overflow-x:auto"><table class="small" style="border-collapse:collapse;min-width:100%;text-align:right"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div></div>`;
  }).join("");
}
function _mxComparables(p) {
  const c = p.comparables || [];
  if (!c.length) return "";
  return `<details style="margin-top:8px"><summary class="small"><b>Similar past matchups</b> (${c.length}, every one used, wins and misses alike)</summary>${_mxDetail(c.map((x) => ({ date: x.date, opp: x.opp, similarity: _mxNum(x.similarity, 2), weight: _mxNum(x.weight, 3), stats: Object.entries(x.stats || {}).map(([k, v]) => `${_mxStat(k)} ${v}`).join(", "), why: x.why })))}</details>`;
}
// The engine's component breakdown (e.g. a QB's expected designed runs,
// scrambles and kneels; a back's carries and his yards before vs after
// contact): rows of {label, value, unit, kind, basis}, drawn as the facts are.
function _mxBreakdown(p) {
  const rows = Array.isArray(p.breakdown) ? p.breakdown : [];
  if (!rows.length) return "";
  const body = rows.filter((r) => r && typeof r === "object").map((r) => {
    const v = { value: r.value, unit: r.unit, kind: r.kind, basis: r.basis };
    const range = _mxOk(r.lo) && _mxOk(r.hi) && r.hi > r.lo ? ` <span class="small" style="color:var(--muted)">(${_mxNum(r.lo, 2)} to ${_mxNum(r.hi, 2)})</span>` : "";
    const basis = r.basis ? `<div class="small" style="color:var(--faint,var(--muted));font-size:11px">${_mxEsc(String(r.basis).slice(0, 160))}</div>` : "";
    return `<tr title="${_mxEsc(r.basis || "")}"><td style="text-align:left;color:var(--muted)">${_mxEsc(r.label || "")}${basis}</td><td>${_mxFactVal(v)}${range}</td><td>${_mxKind(r.kind)}</td></tr>`;
  }).join("");
  return `<div style="margin-top:8px"><div class="small"><b>Where the projection comes from</b> <span style="color:var(--muted)">- the model's components, each with its basis</span></div><table class="small" style="border-collapse:collapse;width:100%;text-align:right">${body}</table></div>`;
}
function _mxPlayerCard(p, idx, compact) {
  const st = p.status || {};
  return `<div style="border:1px solid var(--line,#333);border-radius:8px;padding:8px 10px;margin-bottom:10px">
    <div><b>${_mxEsc(p.name)}</b> <span class="small" style="color:var(--muted)">${_mxEsc(p.pos || "")} · ${_mxEsc(p.team)} v ${_mxEsc(p.opp)}${st.value ? ` · status ${_mxEsc(st.value)}${st.source ? ` (${_mxEsc(st.source)}${st.ts ? `, ${_mxEsc(_mxMt(st.ts))}` : ""})` : ""}` : ""}</span></div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;margin-top:6px">
      <div><div class="small"><b>How he produces</b></div>${_mxFacts(p.role)}</div>
      <div><div class="small"><b>What ${_mxEsc(p.opp)} concedes</b></div>${_mxFacts(p.opponent_profile)}</div>
    </div>
    <div style="margin-top:8px"><div class="small"><b>Matchup matrix</b> <span style="color:var(--muted)">- tap a row for its data; the effect column is what the model actually applied</span></div>${_mxMatrix(p, idx)}</div>
    ${_mxBreakdown(p)}
    ${_mxMarkets(p, compact)}
    ${_mxComparables(p)}
    ${(p.notes || []).length ? `<div class="small" style="color:var(--muted);margin-top:6px">${p.notes.map(_mxEsc).join(" · ")}</div>` : ""}
  </div>`;
}
function _mxPicker(sel, act) {
  const ps = [...(_mx.data.players || [])].sort((a, b) => (String(a.team) + a.name).localeCompare(String(b.team) + b.name));
  return `<select data-mx="${act}" style="width:auto;padding:2px 4px"><option value="">- pick a player -</option>${ps.map((p) => `<option value="${_mxEsc(p.player_id)}"${String(sel) === String(p.player_id) ? " selected" : ""}>${_mxEsc(p.name)} (${_mxEsc(p.team)} v ${_mxEsc(p.opp)})</option>`).join("")}</select>`;
}
// One player's card from his game's full piece, or what is happening to it.
function _mxCardFor(id, idx, compact) {
  if (!id) return `<div class="empty">${idx ? "Pick a player." : "Pick a player, or open one from Opportunities."}</div>`;
  if (!_mxGameOf(id)) return `<div class="empty">${(_mx.data || {}).live_filtered ? "He is not on the list: his game is under way or over (the next day's board shows from midnight, Mountain time), or he is not on this date's slate." : "He is not on this slate - pick a player from this date's list."}</div>`;
  const piece = _mx.full[_mxGameOf(id)];
  if (!piece || piece.loading) return `<div class="empty">Loading his matchup detail…</div>`;
  if (piece.error) return `<div class="empty">${_mxEsc(piece.error)}</div>`;
  const p = _mxFull(id);
  return p ? _mxPlayerCard(p, idx, compact) : `<div class="empty">He is not in his game's published detail - reload the slate.</div>`;
}
function mxPlayerView() {
  return `<div class="small" style="margin-bottom:6px">${_mxPicker(_mx.player, "pick")}</div>${_mxCardFor(_mx.player, 0)}`;
}
function mxCompareView() {
  return `<div class="small" style="margin-bottom:6px">Two players, side by side - e.g. two receivers against the same defense: ${_mxPicker(_mx.cmp[0], "cmp0")} vs ${_mxPicker(_mx.cmp[1], "cmp1")}</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:10px">${_mx.cmp.map((id, i) => _mxCardFor(id, i + 1, true)).join("")}</div>`;
}
