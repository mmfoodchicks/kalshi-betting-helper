# Independent review: the Matchups scanner's Side and Sort controls (+ the `breakdown` section)

Reviewer: a fresh instance with no part in the change. Written 2026-10-23 ~21:00 UTC (15:00 MDT).
Scope: the uncommitted diff to `static/matchups.js` and `tests/guards_matchups.py`, read against
`prop_value.py` (`line_value`, `ev_interval`, `kalshi_reference`, `line_labels`), `matchups.py`
(`mark_record`, `_slim_line`, `_VALUE_KEYS`) and the payload contract. No repo file was edited
(only this one was written); the working-tree files are byte-identical before and after
(`static/matchups.js` sha256 `45cd3cd9...efaa`, `tests/guards_matchups.py` `88a16b75...e842f`, checked
after the mutation runs). Scratch work (mutation sandbox, fixture, Playwright scripts, fuzzers,
screenshots) is under `/tmp/claude-0/-home-user-kalshi-betting-helper/2a51b1d4-3290-577b-b97b-fbbf27fce813/scratchpad/`
(`mut/`, `mutate.py`, `fuzz.js`, `fuzz2.js`, `ui/`).

How it was exercised: `python3 tests/guards_matchups.py` (32/32 green, pyflakes clean on the guard
file); 22 mutations of `matchups.js` run against the guard on a symlinked COPY of the tree; two node
fuzzers over the real `mxScanView` (60,000 renders with null / NaN / Infinity / string / missing
values in every field, 15,000 sorts checking the ordering invariants); the real app
(`VIGIL_NO_BG=1`, scratch cache dirs, port 57209, stopped by PID and verified gone) under Chromium
via Playwright, at 1280 px and 390 px, against a fixture published with `matchups.publish` whose
`value` / `labels` come from `prop_value.line_value` / `line_labels` (not hand-typed), including
hostile strings in names, `breakdown` and localStorage.

---

## Verdict: AGREE AFTER FIXES

One major finding (a stale-quote line tops "Best picks"; a one-line fix) and a set of guard gaps
worth closing in the same commit. Everything else is minor or nit. The side math, the band flip, the
sort invariants, the escaping and the layout are right.

---

## Findings

### F1. major - "Best picks" ranks a stale-quote line first, above a line the engine labelled Robust +EV

`static/matchups.js:369-375` (the `best` key). The clean tier is
`r.b && r.b.ok && !r.b.mkt && r.b.ev > 0`. `b.ok` is `value.believable` (the book), `b.mkt` is
`market_better`; nothing reads `stale_quote`. `prop_value.line_labels` withholds `positive_ev` on a
quote older than 600 s (or of unknown age) and returns the qualifier `stale_quote` instead, but
`believable` stays true, so such a line passes the clean test on its raw EV.

Reproduced in the app: fixture line Bravo pts 22+ (YES 40c, quote 2,000 s old, EV +13.3c, floor +8.3c,
labels `['low_evidence', 'stale_quote']`, no `positive_ev`) is row 1 under the default Best picks / Best
side, above Alpha pts 20+ NO (+11.4c, floor +6.4c, labels `positive_ev`, `positive_ev_robust`). The
panel text promises "believable +EV lines"; the engine itself refused the +EV label on row 1. The EV
cell is bold and un-muted as well (pre-existing, `_mxEvCell`). How often: any slate whose Kalshi cache
was older than 600 s when the board was built (`nfl_matchup.py:1844` stamps every quote with the
index's build time), or a quote with no timestamp ("an unknown age counts as stale").

Fix: add the label to the demotion test, the same way thin books are demoted:

```js
const stale = (r.l.labels || []).includes("stale_quote");
return r.b && r.b.ok && !r.b.mkt && !stale && r.b.ev > 0 ? _mxKey(c) : -1e11 + ...
```

Optionally have `_mxEvCell` print "(stale quote)" muted, as it does for thin books. Add a stale line
to the guard fixture (see F5) so the check is red without the fix.

### F2. minor - the NO reading of a one-sided book reverses the direction of the bound

`static/matchups.js:170-175` (`_mxVsMkt`), `:365` (`sideEdge`). `prop_value.kalshi_reference` says a
one-sided `p_market` is "an upper bound for an ask and a lower bound for a bid". Flipping it for NO
(`1 - p_market`) turns the ask-only upper bound on P(YES) into a LOWER bound on NO's fair price, so the
NO edge it yields is an overstatement where the YES edge was a conservative understatement. The only
caveat printed is the raw `market_kind` string ("one_sided"). Observed: fixture Bravo pts 18+ (YES ask
60c, no YES bid, no NO offer: "60c / -") reads "NO 20% | mkt 40% one_sided | -20.0 pts | -" under
Side NO. With the model at YES 20% that same line would read NO +40 pts and sit at the top of
"Biggest edge vs market" with no NO contract to buy (`ev_no` null). The YES edge sort has the mirror
problem for bid-only books (pre-existing); the flip makes the common ask-only case point the
anti-conservative way.

Fix (smallest): for `side === "no"` and `v.market_kind !== "two_sided"`, `_mxVsMkt` prints the market
share with "(floor)" or just "-", and `sideEdge` returns null (sorts last via `_mxKey`).

### F3. minor - the demoted tier of "Best picks" mixes muted +EV lines with clean -EV lines

`static/matchups.js:369-375`. Everything not clean falls to `-1e11 + c/1e3`, ordered by the cautious EV
`c = min(lo, ev)`. A believable -EV line (ev -0.1c, floor -0.3c, c = -0.3) therefore outranks a muted
+EV line (market better / thin / stale; ev +4c, floor -1.2c, c = -1.2). Not a bug against the spec
("in the same cautious order") but it is not what a reader of "thin books go after" expects, and it
matters on the NBA: `matchups.market_record("nba")` is the single `"*"` record (Kalshi 0.542 vs model
0.556 log loss), so `mark_record` puts `market_better` on EVERY +EV NBA line and the clean tier is
EMPTY; the whole NBA "Best picks" list is the demoted order, i.e. +EV lines interleaved with -EV ones
below their floors. Suggest two sub-tiers: demoted +EV (`ev > 0`) above demoted not-+EV, e.g.
`-1e11 + (r.b && r.b.ev > 0 ? 0 : -1e10) + c/1e3`.

### F4. minor - "Best side" is not byte-for-byte the old behaviour (three places)

1. The EV column's mute rule changed. `_mxBest` (`:157`) now also sets `mkt` when `ev > 0` and the
   slate's `market_record` impeaches the stat even WITHOUT a `market_better` label (stale-quote lines,
   F1). It mirrors `mark_record`'s intent and is arguably right, but it alters "Highest EV" and the
   player page (`:470` passes `m.stat`), and the chip row will not show "market better on record" on
   such a line while the EV cell says it. No guard pins it (mutation M14 green).
2. In Best side mode, "Model (side)" and the `p` sort now use the best-EV side's probability, where
   before both used YES `p`. A 90% YES at 99c with +EV on the 3c NO now reads "NO 10%" and sorts to
   the bottom of "Highest probability" instead of the top. Consistent with the column, but "Highest
   probability" in Best side mode does not mean "most likely outcome". Say so in the sort's tooltip, or
   key `p` on `max(p, 1 - p)` in Best side mode only.
3. A line with no market now shows a side word ("YES 54%") that was never printed before
   (`_mxRowSide` falls back to "yes" when `b` is null). Cosmetic.

### F5. minor - the new guards pin the headline cases but leave 12 of 22 mutations green

Mutation results (22 run; copy of the tree; "RED" = `guards_matchups` fails):

| RED (caught) | GREEN (survives) |
|---|---|
| M1 Best picks = plain EV (no band floor) | M3 `_mxVsMkt` does not flip `p_market` for NO |
| M2 NO `p` not flipped | M5 `sideEdge` not negated (edge sort in NO mode) |
| M2b band flipped but not swapped | M6 `ev > 0` dropped from the clean test |
| M4 `_mxVsMkt` edge not negated | M8 NO interval read from `ev_interval.yes` |
| M7 NO EV read from `ev_yes` | M9 `_mxRowSide` ignores the chosen side (only differs when `b` is null) |
| M10 `mkt` demotion dropped | M11 `p` sort unflipped for NO |
| M10b thin-book (`ok`) demotion dropped | M14 `_mxImpeached` ignores the record |
| M12 default sort back to "ev" | M15 sort select not validated |
| M13 "best" side falls to YES | M16 `_mxRestore` unvalidated |
| M18 Best picks keyed on the band's top | M17 sort select does not reset `dir` |
| | M19 side select not validated, M20 YES band returned flipped |

So the two things the owner named are covered: reverting Best picks to plain EV is red (M1), and so is
the NO-side flip of `p` and of the band (M2, M2b), the NO EV (M7) and the edge shown (M4). Gaps worth
closing in `tests/guards_matchups.py` (fixture at lines 392-401, assertions 417-420):

- `p_market: 0.5` makes `1 - p_market == p_market`, so M3 cannot fail. Use 0.4 and assert `mkt 60%`.
- No clean -EV line, so M6 cannot fail: add a believable line with ev -2, floor -4 beside a +EV line
  with floor -5 (the +EV line must rank first).
- No stale line: add `labels ['stale_quote']`, believable, ev +20 and assert it ranks after every clean
  line (this is F1's regression).
- Nothing runs `p` or `edge` sort, in either side: assert the order for `sort='p'` and `'edge'` with
  `side='no'` (M5, M11).
- `set(o3["best"][3:]) == {3, 4}` leaves the order of the demoted pair free; the cautious order says
  `[3, 4]` (floors +28 vs +18).
- The persistence and select handlers are untested: call `_mxRestore` against a stub `localStorage`
  (hostile JSON, `null`, `{"sort":"x"}`) and `_mxChange` with a fake event for `sortsel` / `side`.
- The assertion `bool(o3) and ...` is fine, but like the existing XSS guard it passes vacuously when
  `node` is absent (`so_ok is not False`). GitHub's ubuntu runner has node, so CI is covered; worth a
  one-line comment.

### F6. minor - the label filter ignores the Side select

Side = NO plus the "+EV" chip keeps every line whose `positive_ev` label belongs to the YES side and
shows its NO numbers: observed rows "NO 85% ... NO -5.6c", "NO 60% ... NO -8.5c" under the +EV filter.
The chip names its own side, but the combination reads as a contradiction. Cheap fix: when `_mx.side`
is "yes"/"no" and the label is `positive_ev` / `positive_ev_robust`, also require `r.b && r.b.ev > 0`.
Or document that Side is a lens, not a filter (the panel text does say "Side: ..." only).

### F7. nit - `breakdown` rows drop their `lo` / `hi`

`nfl_rush.item` emits `lo` / `hi` (None unless a shipped coefficient sets them); `_mxBreakdown`
(`:502`) reads neither, and builds `n` it never uses. The contract only lists
`label/value/unit/kind/basis`, so either draw a band when both are present or note in the contract that
the tab ignores them, so a producer is not surprised. An unknown `kind` (the fixture's "constructor")
renders as plain unstyled text, harmless.

---

## Checked and correct

**Side math.** `_mxSideP` (`:139`): NO gives `p = 1 - p`, `lo = 1 - p_hi`, `hi = 1 - p_lo`, each guarded
by `_mxOk`; `_mxBand` still prints "no band" on a zero-width or null band (fixture "NO 85% no band").
`prop_value.ev_interval` returns `[unfavourable, favourable]` for both sides (NO: `[EV(1-p_hi), EV(1-p_lo)]`),
so `lo = iv[0]` really is the cautious end for NO; the fixture shows NO +11.4c with "+6.4 to +16.4".
Edge for NO is `-edge` (model NO share minus market NO share, algebraically exact) and the market
share is `1 - p_market` (two-sided books: "mkt 39%" on YES, "mkt 61%" on NO). The chosen side's EV and
interval come from `ev_no` / `ev_interval.no` (`:129-137`); a side with no offer returns null and draws "-".

**Sort keys.** Missing values go last through `_mxKey` / the `-1e9` branch; 60,000 fuzzed renders (nulls,
NaN, Infinity, strings, `value: {}`, hostile `best_side`, `stat` = "constructor" / "__proto__") threw
nothing and printed no NaN / undefined / Infinity. 15,000 sorts across Best / YES / NO confirmed: every
clean line precedes every demoted one, the clean tier is monotone in cautious EV, the demoted tier is
monotone in cautious EV. The magnitude (`-1e11 + c/1e3`) resolves differences down to ~1e-5, below any
visible EV difference. The `dir` toggle from the column headers works (EV header from Nef -> EV, again ->
ascending, demoted first as before); the Sort select resets `dir` to -1; there is no header for "best",
so `dir` cannot reach +1 on it. The header click now persists the sort as well (`:224`).

**Filters.** Stat / game filters and the "only lines Kalshi lists" tick run before the sort and are
independent of Side (rows are built once with the chosen side); unlisted lines have `value: null`, so
`b` is null and they sort last under every key. The label chip filter: see F6.

**Persistence.** `_mxRestore` runs once in `initMatchups`, whitelists `sort` against `_MX_SORTS` and
`side` against `["best","yes","no"]`; try/catch around parse and access. Fed `null`, `"x"`, `[1]`,
`{not json`, `{"sort":"<img ...>","side":"constructor"}` in the browser it fell back to Best picks / Best
side every time, no script ran; `{"sort":"nef","side":"no"}` restored. Reload kept the chosen
selections. `_mxChange` re-validates the select values (an invented `<option value>` falls to "best").

**Safety.** Every new interpolation is a constant (`_MX_SIDES` / `_MX_SORTS`, `th()` keys), whitelisted
(`_mxSide`, applied to `_mxRowSide` whose `b.side` comes only from "yes"/"no" literals) or escaped
(`_mxEsc` on breakdown `label`, `basis` in the text AND the `title` attribute, `unit`, and through
`_mxFactVal` for a non-numeric `value`). `escapeHtml` in `app.js` escapes `& < > " '`. A fixture with
`<img src=x onerror=window.__xss=1>` in a breakdown label / unit / basis / kind and in a player name
rendered as text: `window.__xss` undefined, zero `<img>` in `#mxBody`. No inline event handlers were
added (every control uses `data-mx`, handled by the existing delegated listeners; a click on a `<select>`
is ignored by `_mxClick`, the change goes through `_mxChange`).

**Layout at 390 px.** No horizontal page scroll (`scrollWidth == clientWidth == 390`) in the scanner or
on the player page; the Side / Sort row wraps into two lines; the table sits in its own scroller; the
breakdown table renders label + basis, value + unit, kind chip legibly (`phone_scan2.png`,
`phone_bd.png` in the scratch `ui/` folder). The explanatory sentence next to the Sort select is long
on a phone but wraps cleanly.

**Plumbing.** `breakdown` survives `matchups.publish` (the per-game pieces keep the full player dict) and
`_json_finite`; the index does not carry it, which is right (the scanner never reads it). The
`mkt` record fallback reads `market_record`, which is in `_TOP_KEYS`, so the index has it. The asset
version (`app._asset_version`, `sw.js` precache) already covers `matchups.js`.
`${_mxBand(` still occurs three times, so the existing F3 guard holds; the F18 guard's substring
(`mkt: (labels || []).includes("market_better")`) still matches the extended line.

---

## Suggested edits, in order of value

1. F1: `!(r.l.labels || []).includes("stale_quote")` in the `best` clean test (+ a stale fixture line).
2. F5: fixture `p_market` 0.4, a clean -EV line, `p` / `edge` orders under NO, the `[3, 4]` pin, a
   `_mxRestore` / `_mxChange` check.
3. F2: no NO-side market share / edge for a non-two-sided book.
4. F3 and F6 as taste allows; F4(2) a tooltip on "Highest probability".

No blocker. Re-run `python3 tests/guards_matchups.py` after the edits (they run in ~0.5 s) and check
the exit code before the batched push.

## Orchestrator's response (2026-10-23)

- F1 fixed: a stale quote is its own muted tier member (`stale` on both value builders; the EV cell reads
  "(stale quote)", never bold); the sort's clean test requires it fresh.
- F2 fixed: NO on a book that is not two-sided shows "-" for vs-market and keys its edge as missing.
- F3 fixed: three tiers under the clean +EV lines -- muted +EV, then negative EV, then no EV -- each in the
  same cautious order.
- F5 fixed: the harness now uses p_market 0.4, a clean -EV line, a stale line, a one-sided NO line and a thin
  +EV line whose floor is -30c; it pins all five orders exactly, the NO probability / edge sorts, the chip
  filter under NO, and `_mxChange` / `_mxRestore` fed bad values. Reverts of F1, F2, F3 and F6 each turn it red.
- F6 fixed: with YES or NO chosen, the +EV chips keep only lines whose chosen side's EV is positive.
- F7 fixed: a breakdown row's lo / hi (when it has them) print as a range beside the value.
- F4 kept on purpose: (a) a +EV line in a stat the record impeaches is muted whether or not its label carried
  the qualifier -- the record is the reason, not the label; (b) in Best-side mode the probability column and its
  sort read the side whose EV is shown, so the row reads as one bet; (c) a line with no market shows its YES
  side's probability labelled "YES", which is what that number is.

---

## RE-CHECK (reviewer, 2026-10-23 ~21:25 UTC / 15:25 MDT)

Read against the tree as it stands (`static/matchups.js` sha256 `68c09fc1...d9fba`,
`tests/guards_matchups.py` `a7014cb2...e14e`; both unchanged by me, confirmed after every run).
`python3 tests/guards_matchups.py`: 32/32, exit 0; pyflakes clean. The app was restarted on the same
fixture (port picked free, stopped by PID, verified gone) and driven again under Chromium at 1280 and
390 px.

### The fixes, in the code and in the app

| Finding | Code | Observed |
|---|---|---|
| F1 stale quote | `stale` flag on both value builders (`_mxSideValue`, `_mxBest`); `clean = ok && !mkt && !stale`; `_mxEvCell` draws it muted "(stale quote)", never bold | Default view: Alpha NO (+11.4c) first; Bravo 22+ (the 2,000 s quote) now sits in the muted group after the clean +EV lines, reading "YES +13.3c (stale quote)", above only the market-better line |
| F2 one-sided NO | `_mxVsMkt` returns "-" for NO on a non-two_sided book; `sideEdge` returns null for it | Side NO, Bravo 18+: "60c / -", vs market "-", EV "-" (was "mkt 40% one_sided -20.0 pts") |
| F3 tiers | `demoted`: -1e11 for muted +EV, -2e11 for negative EV, -1e12 for no EV, each in cautious order | Fuzz: 15,000 sorts (Best / YES / NO) with tiers 3 (clean +EV) > 2 (muted +EV) > 1 (the rest with an EV) > 0 (no EV), every tier monotone in cautious EV: 0 violations. 60,000 hostile-value renders: no throw, no NaN / undefined / Infinity text |
| F6 chip filter | with YES / NO chosen, `positive_ev` / `positive_ev_robust` also need `r.b.ev > 0` | Side NO plus the +EV chip: one row (Alpha 20+ NO +11.4c). Before: five rows, four with negative NO EV |
| F7 range | breakdown prints `(lo to hi)` when both are finite and `hi > lo` | Not drawn in my fixture (no `lo`/`hi` rows); code read, `_mxNum(.., 2)` on finite values only |
| F5 guards | see the mutation run below | |

Nothing else regressed: persistence and hostile-storage cases (null, "x", [1], bad JSON, a script-tag
sort and "constructor" side) fall back to Best picks / Best side with `window.__xss` undefined; reload
keeps a stored `{"sort":"nef","side":"no"}`; header EV click toggles to ascending; no horizontal scroll
at 390 px on the scanner or the player page; no inline handlers; every new interpolation is escaped or a
constant. The remaining console errors are the sandbox's blocked fonts / other tabs' endpoints, as
before.

### Mutation run (34 mutations on a symlinked copy; each restores the original file after)

- **RED (26):** best = plain EV (M1) and the band top (M18); NO p not flipped (M2), band not swapped
  (M2b), `p_market` not flipped (M3, was green), edge not negated in the cell (M4) and in the sort (M5,
  was green), NO EV read from `ev_yes` (M7), NO interval read from yes (M8, was green), `ev > 0` dropped
  from the clean test (M6, was green), `mkt` / thin demotion dropped (M10, M10b), **F1 reverted (M10c)**,
  `p` sort unflipped (M11, was green), default sort back to "ev" (M12), "best" side collapsed to YES
  (M13), sort select / side select / stored sort / stored side unvalidated (M15, M19, M16, M16b, all were
  green), **F2 reverted in either place (M21, M22)**, **F3 reverted (M23)**, **F6 reverted (M24)**,
  stale flag dropped in `_mxBest` (M27), the old `ev` key (M30).
- **GREEN (8):** M9 (`_mxRowSide` ignores the chosen side: equivalent unless `b` is null, harmless),
  M14 (`_mxImpeached` ignores the record: only the label path is pinned), M17 (sort select does not reset
  `dir`), M20 (the YES band printed flipped), M25 (F7's range), M26 (the "(stale quote)" text), M28
  (stale flag dropped in `_mxSideValue`, i.e. F1 under an EXPLICIT Side), M29 (a stale EV printed bold).

So reverting Best picks to plain EV and the NO-side flip are both red, as are reverts of F1, F2, F3 and
F6. The ones still open are nits, none of them one of the owner's named behaviours.

### New, all nit

- **R1 nit.** `stale` is set from the line's `stale_quote` label whatever the chosen side's EV: the label
  exists only because the YES side earned +EV, so Side NO shows "NO -18.6c (stale quote)" on a negative
  NO row (observed on Bravo 22+). The quote is in fact old, so it is true, just noisy; the `mkt` flag uses
  `ev > 0 &&` and `stale` could too.
- **R2 nit.** M28: add one `side = 'yes'` order assertion with the stale line (it should land after the
  clean +EV YES lines) so F1 is pinned for explicit Side as well as Best side. M25 / M26 / M29 are
  one-liners to pin if wanted.

### F4, kept on purpose: accepted

(a) Muting by the record rather than the label is the right reason and now agrees with the Python
`mark_record` rule; (b) one probability per displayed bet is coherent and is what the column header
"Model (side)" says; (c) "YES 54%" on a line with no market is the number's true label. No change asked.

### RE-CHECK verdict: AGREE TO SHIP

The major finding is fixed and pinned (M10c red), the minors are fixed and pinned (M21-M24 red), the
guard hole I flagged is closed (12 greens to 8, the survivors nits), the sort / side behaviour matches the
owner's request in the running app, and the working-tree files are byte-identical to what I read. Run the
full `python3 tests/combo_audit_guards.py` with its exit code checked as usual before the batched push.
