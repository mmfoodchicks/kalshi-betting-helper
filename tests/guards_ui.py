"""Guards for the readability pass (2026-11-26; the owner: "make the website
easier to read and use. Even though I'm the only one using it I still want it
to be presentable"). Each check pins a defect seen on a phone-width screenshot
of the running app and the rule that fixed it:

- a checkbox drew on a line of its own above its caption (the base `label`
  rule stacks a label's children for form fields);
- `.chip` was used in thirteen places and styled in none, so the NFL ladders
  ran together and wrapped between a line and its percentage;
- Best Bets' source pills broke inside the pill ("nhl:" on one row, "0" on the
  next), named four sources by their raw keys, and drew MLB's deliberate
  off-season pause as a failure;
- every card opened on a paragraph of method notes (the NFL week board's ran
  seven lines before the first game): the static notes now fold to two lines;
- the NFL card's two models ran as one line that wrapped to four, and Kalshi's
  asks were drawn inside the win bar, over its fill;
- the bet ledger offered Baseball or Other, with a crypto example (crypto was
  removed on 2026-10-29).

Static checks on the page's own files; the layouts themselves were checked on
screenshots at 412 px. Hermetic: reads files only.

    python3 tests/guards_ui.py       # exits 1 on any failed check
    from tests.guards_ui import run  # run(ck) inside the main suite
"""
import os
import re
import sys

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _read(*parts):
    with open(os.path.join(_ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _between(src, start, end):
    i = src.index(start)
    return src[i:src.index(end, i)]


def run(ck):
    css = _read("static", "style.css")
    js = _read("static", "app.js")
    html = _read("templates", "index.html")

    ck("UI: a checkbox or radio sits beside its caption -- the base label rule stacks a label's children, so a "
       "rule of its own turns a label holding one back into a row",
       'label:has(> input[type="checkbox"]), label:has(> input[type="radio"]) {' in css
       and "flex-direction: row" in _between(css, 'label:has(> input[type="checkbox"])', "}"))

    chip = re.search(r"^\.chip \{([^}]*)\}", css, re.M)
    uses = len(re.findall(r'class="chip"', js))
    ck("UI: .chip, used across the boards, has a style of its own and never wraps inside itself",
       bool(chip) and "white-space: nowrap" in chip.group(1) and "inline-block" in chip.group(1) and uses >= 10,
       f"rule={bool(chip)} uses={uses}")

    lean = re.search(r"^\.leanchip \{([^}]*)\}", css, re.M)
    ck("UI: a lean/source pill is one line -- inline spans broke inside the pill on a phone",
       bool(lean) and "white-space: nowrap" in lean.group(1) and "inline-block" in lean.group(1)
       and "text-overflow: ellipsis" in lean.group(1))

    bb = _between(js, "const _SRC_LABEL", "async function loadBestBets")
    src_keys = re.findall(r'pull\("([a-z_]+)"', _read("bestbets.py"))
    missing = [k for k in src_keys if f"{k}:" not in bb]
    lb = _between(js, "async function loadBestBets", "if (!d.rows || !d.rows.length)")
    ck("UI: Best Bets names every source bestbets.board pulls (no raw keys on the chips), draws a source switched "
       "off on purpose as 'paused' in the quiet colour rather than a failure, and escapes the error in its title",
       bool(src_keys) and not missing and 'const paused = !s.ok && /paused/i.test(s.error || "");' in lb
       and 's.ok || paused ? "" : " warn"' in lb and 'paused ? "paused" : "✗"' in lb
       and 'title="${escapeHtml(s.ok ? "" : (s.error || "unavailable"))}"' in lb,
       f"sources={src_keys} missing={missing}")

    fold = _between(js, "function foldLongNotes()", "function _foldCheck()")
    check = _between(js, "function _foldCheck()", "// ---- Best Bets")
    boot = _between(js, "  // Baseball setup\n  setupTabs();", "loadBestBets();")
    ck("UI: the static intro notes fold to two lines with a toggle -- run ONCE at load, right after the tabs are "
       "wired, so a note a board renders at run time (a warning, an empty state) is never folded; only p.sub; "
       "each note folded once; the toggle says its state (aria-expanded) and hides wherever the note fits",
       "setupTabs();\n  foldLongNotes();\n" in boot and js.count("foldLongNotes(") == 2
       and 'document.querySelectorAll("p.sub")' in fold and "if (p.dataset.fold ||" in fold
       and 'p.classList.add("folded")' in fold and 'b.setAttribute("aria-expanded", String(open));' in fold
       and "b.hidden = p.scrollHeight <= p.clientHeight + 1;" in check and "!p.offsetParent" in check
       and re.search(r"\.sub\.folded \{[^}]*-webkit-line-clamp: 2", css) is not None
       and ".foldbtn[hidden] { display: none; }" in css,
       f"calls={js.count('foldLongNotes(')}")

    card = _between(js, "function nflSlateCard", "function renderNFLWeek")
    lbl = _between(card, '<div class="lbl">', "</div>")
    ck("UI: the NFL card's win bar holds the two probabilities only; Kalshi's asks and our edge on each sit on "
       "their own line under it, and the two models are a small table (model, away, home) with the deep figure's "
       "game count and standard error kept beside it",
       "_nflEdgeChip" not in lbl and '<div class="nfl-kx">' in card and card.count("_nflEdgeChip(") == 2
       and '<div class="nfl-models"' in card and card.count('<span class="nm-name">') == 2
       and "sims, ±${(100 * Math.sqrt(g.p_home_deep * (1 - g.p_home_deep) / g.deep_n_sims)).toFixed(1)} pts"
       in card and ".nfl-models .nm-grid { display: grid;" in css and ".nfl-kx {" in css, lbl[:160])

    kinds = re.findall(r'<option value="([a-z]+)">', _between(html, '<select id="betKind">', "</select>"))
    ck("UI: the bet ledger's types are the sports Vigil covers (Baseball and Other were all it offered) and its "
       "example is not the removed crypto feature",
       {"nfl", "cfb", "nba", "nhl", "soccer", "ufc", "tennis", "golf", "baseball", "dfs", "other"} <= set(kinds)
       and kinds[-1] == "other" and "BTC" not in html, str(kinds))

    phone = _between(css, "@media (max-width: 640px) {\n  .topbar-inner", "/* Section header")
    ck("UI: on a phone the header drops the tagline and the plan hint (two rows above every tab)",
       ".topbar .sub, .settingsbar .hint { display: none; }" in phone)


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
