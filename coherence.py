"""Does the market agree with ITSELF? Model-free incoherence finder for tiered
futures books, plus the nightly snapshot that gives October a record instead of
a memory.

Every other edge on the site is model-vs-market, which is only as good as the
model — and the model's graded record is thin. These checks need no model at
all: they are arithmetic on the venue's own prices, and when they fire the
market is contradicting itself no matter whose projection is right.

What was live the night this was built (2026-08-08, Kalshi MLB):

  * The Dodgers were priced to win 78.5% of the World Series they REACH
    (WS 36.9c against a 47c pennant). Every other contender sat between 30%
    and 58%. A best-of-seven between two league champions has never been a
    79/21 proposition for anyone.
  * The NL was priced to win 63% of all World Series (de-vigged league split),
    a 2-of-3 claim about a coin-flip-shaped event.

The three checks, all on the venue's own numbers:

  CONDITIONAL   For each team quoted in both tiers, P(win WS | win pennant) =
                ws_share / pennant_share (shares book-normalized within their
                own tier, so each tier's vig cancels against itself). A
                best-of-seven between the two best teams in baseball lives in
                a band; a number outside it means the two books disagree about
                the same team.
  LEAGUE SPLIT  De-vigged WS probability summed by league. One league cannot
                win 2 of 3 World Series in advance.
  PARENT/CHILD  A team's WS YES can never cost more than its pennant YES —
                winning the World Series REQUIRES the pennant. Raw cents with
                a tolerance for vig noise.

A flag is not a bet ticket: it says which prices cannot all be right, and by
how much. Which side is wrong is still a judgement.
"""
import json
import os
import time

import clock
import kalshi
import season_sim

# Believable band for P(champion A beats champion B in a best of seven).
# The widest series price a bo7 between two LEAGUE CHAMPIONS supports: even a
# 60/40 per-game favourite (rare between two pennant winners) wins the series
# ~71%; 0.68 leaves room for that with home field while still catching 78.5%.
COND_BAND = (0.28, 0.68)
COND_MIN_PEN_SHARE = 0.04    # ignore longshots: 1c/2c ratios are all noise
LEAGUE_SPLIT_MAX = 0.58      # either league priced above this is a 2-of-3 claim
PARENT_TOL_C = 1.5           # cents of vig slack before parent<child is a flag

_HIST_DIR = os.path.join(os.path.dirname(__file__), "futures_history")


def _tier(series_list, union=False):
    """{abbr: yes_ask cents} for a winner-style tier.

    `union=False`: the list is FALLBACKS for one book (the WS series plus the
    renames it might hide under) — the first series that answers wins.
    `union=True`: the list is SEPARATE books that together make the tier (the
    AL and NL pennant series) — fetch them all. Confusing the two was this
    module's own first bug: stopping after the AL pennant book flagged every
    AL team as impossibly cheap and never saw the NL at all."""
    out = {}
    for series in series_list:
        for abbr, m in season_sim._winner_markets(series):
            out.setdefault(abbr, m.get("yes_ask"))
        if out and not union:
            break
    return {a: c for a, c in out.items() if c}


def _shares(cents_map):
    """Book-normalized shares: each price over the whole book's sum, so the
    tier's own vig cancels. Only valid within one tier."""
    tot = sum(cents_map.values())
    return {a: c / tot for a, c in cents_map.items()} if tot else {}


def _leagues(season):
    """{abbr: league_id} so the split check doesn't hardcode team lists."""
    stand = season_sim._standings(season)
    try:
        import baseball
        ab = baseball._abbr_map(season)
    except Exception:
        ab = {}
    return {ab.get(tid): v["league"] for tid, v in stand.items() if ab.get(tid)}


def check(season=None):
    """Run every coherence check against Kalshi's live MLB futures books.
    Returns {"flags": [...], "checks": {...}}; empty flags = the books agree."""
    season = season or str(clock.today_et().year)
    ws = _tier(season_sim._WS_SERIES)
    pen = _tier(list(season_sim._PENNANT), union=True)
    po = _tier([season_sim._PLAYOFFS])
    flags, checks = [], {}
    lg_of = _leagues(season)

    ws_sh = _shares(ws)
    # Pennant probabilities normalize WITHIN each league: each league's pennant
    # race is its own one-winner book, and normalizing the two together would
    # let one league's overround lean on the other's teams.
    pen_sh = {}
    for lg in (103, 104):
        pen_sh.update(_shares({a: c for a, c in pen.items() if lg_of.get(a) == lg}))
    checks["ws_book_c"] = round(sum(ws.values()), 1)
    checks["pennant_book_c"] = round(sum(pen.values()), 1)

    # 1. Conditional: P(win WS | win pennant) per team. ws_share estimates
    # P(win WS); pen share within the league estimates P(win pennant); their
    # ratio is the series price the two books jointly imply.
    conds = []
    for ab in sorted(set(ws_sh) & set(pen_sh)):
        pshare = pen_sh[ab]
        if pshare < COND_MIN_PEN_SHARE:
            continue
        cond = ws_sh[ab] / pshare
        conds.append({"abbr": ab, "cond": round(cond, 3),
                      "ws_c": ws.get(ab), "pennant_c": pen.get(ab)})
        if not (COND_BAND[0] <= cond <= COND_BAND[1]):
            hi = cond > COND_BAND[1]
            flags.append({
                "kind": "conditional", "abbr": ab, "value": round(cond, 3),
                "band": COND_BAND, "ws_c": ws.get(ab), "pennant_c": pen.get(ab),
                "size": round(cond - (COND_BAND[1] if hi else COND_BAND[0]), 3),
                "note": (f"{ab} is priced to win {cond * 100:.0f}% of the World "
                         f"Series it reaches - a best-of-seven between league "
                         f"champions lives in {COND_BAND[0]:.0%}–{COND_BAND[1]:.0%}. "
                         f"Its WS price and its pennant price cannot both be right."),
            })
    checks["conditionals"] = sorted(conds, key=lambda c: -c["cond"])

    # 2. League split on the de-vigged WS book.
    split = {}
    for ab, sh in ws_sh.items():
        lg = lg_of.get(ab)
        if lg:
            split[lg] = split.get(lg, 0.0) + sh
    tot = sum(split.values())
    if tot and len(split) == 2:
        norm = {("AL" if k == 103 else "NL"): v / tot for k, v in split.items()}
        checks["league_split"] = {k: round(v, 3) for k, v in norm.items()}
        top = max(norm, key=norm.get)
        if norm[top] > LEAGUE_SPLIT_MAX:
            flags.append({
                "kind": "league_split", "league": top,
                "value": round(norm[top], 3), "max": LEAGUE_SPLIT_MAX,
                "size": round(norm[top] - LEAGUE_SPLIT_MAX, 3),
                "note": (f"The {top} champion is priced to win {norm[top]:.0%} of "
                         f"World Series - one league can't own 2 of 3 in advance."),
            })

    # 3. Parent/child in raw cents: WS <= pennant <= playoffs, each + tolerance.
    for ab in set(ws) | set(pen) | set(po):
        w, p, o = ws.get(ab), pen.get(ab), po.get(ab)
        for child_c, parent_c, child, parent in ((w, p, "WS", "pennant"),
                                                 (p, o, "pennant", "playoffs")):
            if child_c is not None and parent_c is not None \
                    and child_c > parent_c + PARENT_TOL_C:
                flags.append({
                    "kind": "parent_child", "abbr": ab,
                    "child": child, "child_c": child_c,
                    "parent": parent, "parent_c": parent_c,
                    "size": round(child_c - parent_c, 1),
                    "note": (f"{ab}'s {child} YES costs {child_c}c but its "
                             f"{parent} YES only {parent_c}c - the {child} "
                             f"requires the {parent}, so this buys the harder "
                             f"event for more than the easier one."),
                })

    flags.sort(key=lambda f: -abs(f.get("size") or 0))
    return {"generated": clock.now_et().isoformat(timespec="seconds"),
            "season": season, "venue": "Kalshi",
            "n_flags": len(flags), "flags": flags, "checks": checks}


# ---- Nightly snapshot: the record October will be graded against ------------
def snapshot(season=None):
    """Persist tonight's futures board (model vs Kalshi vs Polymarket) plus the
    coherence flags, one JSON per date. The WS disagreement can't be graded
    until October — but only if someone wrote down what everyone said in
    August. Overwrites same-day reruns (the last look of the night wins)."""
    season = season or str(clock.today_et().year)
    date = clock.today_et().isoformat()
    try:
        board = season_sim.board_cached(season)
        rows = [
            {k: t.get(k) for k in ("abbr", "team", "model_pct", "kalshi_cents",
                                   "poly_cents", "edge", "proj_wins")}
            for t in (board.get("markets", {}).get("world_series", {}) or {}).get("teams", [])
        ]
    except Exception:
        rows = []
    rec = {"date": date, "season": season, "ts": int(time.time()),
           "world_series": rows, "coherence": check(season)}
    os.makedirs(_HIST_DIR, exist_ok=True)
    with open(os.path.join(_HIST_DIR, f"{date}.json"), "w") as f:
        json.dump(rec, f)
    return rec


def history_dates():
    try:
        return sorted(f[:-5] for f in os.listdir(_HIST_DIR) if f.endswith(".json"))
    except FileNotFoundError:
        return []


def load_day(date):
    try:
        with open(os.path.join(_HIST_DIR, f"{date}.json")) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None
