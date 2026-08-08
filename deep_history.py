"""Day-over-day history for the deep season sim, with real attribution.

The nightly 4,000-season run overwrites its own cache, so until now every morning
replaced the previous one and there was no way to ask "why did the Dodgers jump
six points overnight?". This keeps a compact snapshot per run and, the moment a
new run lands, works out WHAT CHANGED and WHAT EACH CHANGE WAS WORTH.

Three kinds of thing move a team's number, and they are kept separate because they
mean different things to a reader:

  ROSTER    someone moved to or came off the IL, was called up, or left the 40-man.
  FORM      someone's season line moved sharply -- the eleven-run inning that
            wrecks a reliever's ERA and, with it, the sim's read on him.
  GAMES     the team simply played and won or lost, which shortens the season and
            moves the odds without anything about the club having changed.

ATTRIBUTION. A "+4.1pp" claim is only honest if it is measured, so each event gets
one counterfactual run: today's roster with that ONE player reverted to yesterday's
state, differenced against a today's-roster baseline. Both runs are given the SAME
season seeds (deep_season.run_deep(seed=...)), so they play the same simulated
seasons and the Monte Carlo noise very largely cancels -- common random numbers.
Without that, the noise on a single 4,000-season run swamps the effect being
measured and no affordable number of seasons would recover it. The precision that
survives is measured, not assumed: every figure carries a standard error, and
anything inside twice it is reported as "no measurable effect" rather than as a
small number that looks like knowledge.

Cost is bounded on purpose: _MAX_ATTRIB events per night, chosen by likely impact,
each at _ATTRIB_SEASONS. Everything else is still LISTED, just without a number.

Storage: one small pickle per day under the deep cache (gitignored), plus a single
rolling copy of the previous run's full team profiles -- that is what a
counterfactual reverts against, and it is overwritten each night rather than kept
per-day, because once a day's attribution is computed the profiles are never
needed again.
"""

import os
import time

import clock
import deep_cache

_PREFIX = "mlbhist_"
_PREV_PROFILES = "mlb_prev_profiles"
KEEP_DAYS = 120

# Groups inside a team profile, in the order deep_season reads them.
_GROUPS = ("rotation", "bullpen", "depth", "lineup", "bench", "depth_bats")

# Attribution budget, and what it buys.
#
# Pairing the runs makes a counterfactual EXACT wherever the change did not
# matter -- the engine is deterministic given a seed, so those seasons cancel to
# zero. What is left is real: for a mid-leverage reliever on a contender, about
# 3% of simulated seasons end with a different World Series winner. That residue
# is irreducible, and it sets the precision.
#
# How precise is an attributed figure? MEASURED PER EVENT, not assumed.
#
# The first version of this used one global variance constant. Measuring it three
# times gave 0.029, 0.038 and 0.2365 -- an eight-fold spread -- and two of those
# runs also reported a non-zero drift on a NULL counterfactual, which is
# impossible on an engine already shown to be exactly deterministic. Those two
# had returned fewer seasons than they were asked for; a short run divides by a
# different denominator and injects noise into the difference.
#
# The clean, length-verified measurement put the per-season paired variance at
# ~0.24 for a leverage reliever on the best team in the league -- meaning that
# change alters the World Series winner in roughly a quarter of simulated
# seasons. That number has no business being a constant: a closer and a bench bat
# are nowhere near each other, and a single global figure would be badly wrong
# for both.
#
# So each event's counterfactual is run in BATCHES with different seeds, and the
# spread ACROSS batches is the error bar for that event. Same total seasons, same
# cost, an error bar grounded in the run instead of in a guess. With few batches
# the estimate is itself uncertain, so a two-sided t critical value is used rather
# than 2 sigma, and a figure has to clear it to be shown as a number at all.
#
# Cost at the defaults: (1 baseline + 6 events) x 1,000 seasons on top of the
# nightly. VIGIL_ATTRIB_SEASONS / VIGIL_MAX_ATTRIB dial it without a deploy;
# MAX_ATTRIB=0 disables pricing entirely and the day is still snapshotted and
# diffed, just without pp figures.
_ATTRIB_SEASONS = int(os.environ.get("VIGIL_ATTRIB_SEASONS") or 1000)
_MAX_ATTRIB = int(os.environ.get("VIGIL_MAX_ATTRIB") or 6)
_ATTRIB_BATCHES = 5
# Two-sided 95% t critical values by degrees of freedom (batches - 1). A small
# batch count makes the SD estimate itself noisy, and t is what accounts for that.
_T95 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36, 8: 2.31,
        9: 2.26, 10: 2.23}


def _t_crit(batches):
    return _T95.get(max(1, batches - 1), 2.09)

_IL_NAMES = {"D7": "7-day IL", "D10": "10-day IL", "D15": "15-day IL",
             "D60": "60-day IL", "DL": "IL"}
_IL_CODES = set(_IL_NAMES)


def _is_il(code):
    return (code or "") in _IL_CODES


def _il_name(code):
    return _IL_NAMES.get(code or "", "IL")


# ---------------------------------------------------------------- snapshot ---

def _f(x, d=0.0):
    try:
        return float(x)
    except Exception:
        return d


def _ip(raw):
    """MLB writes innings as '99.1' meaning 99 and 1/3. Convert to a real number
    so day-over-day differences are arithmetic rather than nonsense."""
    s = str(raw or "0").strip()
    try:
        whole, _, frac = s.partition(".")
        return int(whole or 0) + (int(frac or 0) / 3.0 if frac else 0.0)
    except Exception:
        return _f(raw)


def _roster_fingerprint(season, tids):
    """{tid: {pid: compact record}} across the full 40-man, INCLUDING players the
    sim leaves out. A 60-day IL move has to be visible as an IL move rather than
    as a player who silently vanished, which is all team_profile would show."""
    import deep_data
    out = {}
    for tid in tids:
        try:
            lines = deep_data.roster_lines(tid, season) or {}
        except Exception:
            continue
        team = {}
        for pid, r in lines.items():
            rec = {"name": r.get("name"), "pos": r.get("pos"),
                   "status": r.get("status") or "A"}
            # Pitching only. _events raises form flags for arms and not for bats
            # -- a hitter's rest-of-season rate barely twitches on one game -- so
            # carrying batter lines here would be bytes we never read, and this
            # fingerprint is committed to a public repo nightly.
            p = r.get("pit")
            if p:
                rec["ip"] = round(_ip(p.get("ip")), 2)
                rec["er"] = round(_f(p.get("r")))      # runs allowed (season)
                rec["era"] = round(_f(p.get("era")), 2)
            team[int(pid)] = rec
        out[tid] = team
    return out


def snapshot(agg, season):
    """Compact record of one deep run: per-team odds plus a roster fingerprint.
    Deliberately small -- the per-player SIMULATED lines in the agg are the bulk
    of it and are not needed to explain a day-over-day move."""
    n = agg.get("n") or 1
    meta = agg.get("meta") or {}
    teams = {}
    for tid, m in meta.items():
        teams[tid] = {
            "name": m.get("name"), "division": m.get("division"),
            "league": m.get("league"),
            "wins": m.get("wins"), "losses": m.get("losses"),
            "ws": round(100.0 * agg["ws"].get(tid, 0) / n, 2),
            "pennant": round(100.0 * agg["pennant"].get(tid, 0) / n, 2),
            "playoffs": round(100.0 * agg["playoffs"].get(tid, 0) / n, 2),
            "div": round(100.0 * agg["division"].get(tid, 0) / n, 2),
            "mean_wins": round(agg["wins_sum"].get(tid, 0) / n, 1),
        }
    return {"date": clock.today_et().isoformat(), "generated_at": time.time(),
            "season": season, "n": n,
            "n_games_left": agg.get("n_games_left"),
            "teams": teams,
            "roster": _roster_fingerprint(season, list(meta))}


# ----------------------------------------------------------------- storage ---

def _key(date):
    return f"{_PREFIX}{date}"


def save_day(rec):
    deep_cache.save(_key(rec["date"]), rec)
    _shed_old_rosters()
    prune()
    return rec["date"]


def _shed_old_rosters():
    """Keep the roster fingerprint on the newest day only.

    It exists to diff the next run against, and once that diff is done it is dead
    weight -- roughly 100KB per day that would otherwise pile up in the cache and
    in every nightly commit."""
    for d in dates()[1:]:
        rec = load_day(d)
        if rec and rec.get("roster"):
            rec.pop("roster", None)
            rec["roster_shed"] = True
            deep_cache.save(_key(d), rec)


def load_day(date):
    return deep_cache.load(_key(date))[0]


def dates():
    """Every stored snapshot date, newest first."""
    try:
        names = os.listdir(deep_cache.CACHE_DIR)
    except Exception:
        return []
    out = []
    for n in names:
        if n.startswith(_PREFIX) and n.endswith(".pkl"):
            out.append(n[len(_PREFIX):-4])
    return sorted(out, reverse=True)


def prune(keep=KEEP_DAYS):
    for d in dates()[keep:]:
        try:
            os.remove(os.path.join(deep_cache.CACHE_DIR, f"{_key(d)}.pkl"))
        except Exception:
            pass


def previous_date(date):
    """The stored snapshot immediately before `date`, or None."""
    earlier = [d for d in dates() if d < date]
    return earlier[0] if earlier else None


# ------------------------------------------------------------------ events ---

def _pit_like(rec):
    return "ip" in rec or (rec.get("pos") in ("P", "TWP"))


def _events(prev, cur):
    """{tid: [event]} explaining how the roster fingerprint changed.

    Every event carries enough to render a sentence AND to build the
    counterfactual that prices it."""
    out = {}
    for tid, cur_team in (cur.get("roster") or {}).items():
        prev_team = (prev.get("roster") or {}).get(tid)
        if not prev_team:
            continue                      # first sighting of this club: nothing to diff
        evs = []
        for pid, c in cur_team.items():
            p = prev_team.get(pid)
            base = {"pid": pid, "name": c.get("name"), "pos": c.get("pos")}
            if p is None:
                if c.get("status") == "A":
                    evs.append(dict(base, kind="added", to=c.get("status")))
                continue
            ps, cs = p.get("status") or "A", c.get("status") or "A"
            if ps != cs:
                if _is_il(cs) and not _is_il(ps):
                    evs.append(dict(base, kind="il_in", frm=ps, to=cs))
                elif _is_il(ps) and not _is_il(cs):
                    evs.append(dict(base, kind="il_out", frm=ps, to=cs))
                elif cs == "A" and ps == "RM":
                    evs.append(dict(base, kind="called_up", frm=ps, to=cs))
                elif cs == "RM" and ps == "A":
                    evs.append(dict(base, kind="optioned", frm=ps, to=cs))
                else:
                    evs.append(dict(base, kind="status", frm=ps, to=cs))
            # Form: a sharp move in the season line. Pitchers only -- a hitter's
            # rest-of-season rate barely twitches on one game, while a reliever's
            # can be wrecked by a single inning, which is the case worth telling
            # someone about.
            if _pit_like(c) and p.get("ip") is not None and c.get("ip") is not None:
                d_ip = round(c["ip"] - p["ip"], 1)
                d_r = round(_f(c.get("er")) - _f(p.get("er")))
                if d_ip > 0 and d_r >= _BLOWUP_RUNS and d_r >= d_ip * _BLOWUP_RATE:
                    evs.append(dict(base, kind="blowup", ip=d_ip, runs=d_r,
                                    era_from=p.get("era"), era_to=c.get("era")))
        for pid, p in prev_team.items():
            if pid not in cur_team and (p.get("status") or "A") == "A":
                evs.append({"pid": pid, "name": p.get("name"), "pos": p.get("pos"),
                            "kind": "removed", "frm": p.get("status")})
        if evs:
            out[tid] = evs
    return out


# A "blowup" is an outing bad enough to move the sim's read on a pitcher: at least
# this many runs, and at a rate well above a normal bad start, so a six-inning
# five-run start does not trip it.
_BLOWUP_RUNS = 5
_BLOWUP_RATE = 2.5          # runs per inning


# ------------------------------------------------------------- attribution ---

def _revert_player(cur_team, prev_team, pid):
    """A copy of `cur_team` in which `pid` is exactly as they were yesterday --
    same group, same slot, same rates -- and unchanged otherwise.

    Slot matters: rotation order decides who starts which game, so re-adding an
    arm at the end of the list would price a different change from the one that
    actually happened."""
    out = {g: [p for p in (cur_team.get(g) or []) if p.get("id") != pid]
           for g in _GROUPS}
    for g in _GROUPS:
        for i, p in enumerate(prev_team.get(g) or []):
            if p.get("id") == pid:
                out[g].insert(min(i, len(out[g])), p)
                break
    return out


def _impact_rank(ev, team_move):
    """Order events by how much they could plausibly be worth, so the budget is
    spent on the ones a reader would ask about."""
    pos = (ev.get("pos") or "").upper()
    kind = ev.get("kind")
    w = 1.0
    if kind in ("il_in", "il_out", "removed", "added"):
        w = 3.0
    elif kind == "blowup":
        w = 2.0
    elif kind in ("called_up", "optioned"):
        w = 1.5
    if pos in ("P", "TWP"):
        w *= 1.3
    return w * (1.0 + abs(team_move))


def attribute(events, cur_profiles, prev_profiles, season, seasons=None,
              max_events=None, seed=None, team_moves=None, log=None,
              batches=None):
    """Price each event with paired counterfactuals and write `delta_pp` onto it.

    Each event is run in `batches` independent slices, every slice paired against
    a baseline slice on the SAME seed. The mean across slices is the effect; the
    spread across them is its error bar, measured on this event rather than
    assumed from a constant -- a closer and a bench bat differ by an order of
    magnitude and no single constant serves both.

    Events that are not priced (over budget, nothing to revert, or a run that came
    back short) are left with delta_pp = None and say why, rather than showing a
    zero or a number the run cannot support."""
    import deep_season
    seasons = seasons or _ATTRIB_SEASONS
    batches = max(2, batches or _ATTRIB_BATCHES)
    max_events = _MAX_ATTRIB if max_events is None else max_events
    seed = seed if seed is not None else 1_234_567
    team_moves = team_moves or {}
    per = max(1, seasons // batches)

    flat = []
    for tid, evs in events.items():
        for ev in evs:
            flat.append((_impact_rank(ev, team_moves.get(tid, 0.0)), tid, ev))
    flat.sort(key=lambda x: -x[0])
    picked = flat[:max_events]
    if not picked:
        return {"priced": 0, "seasons": seasons, "skipped": 0, "batches": batches}

    def _say(m):
        if log is not None:
            log.append(m)

    def _run(sd, profiles):
        """One slice, or None if it came back short.

        A paired difference is only valid if both sides played the same seasons.
        A short slice divides by a different denominator and manufactures an
        effect out of nothing -- observed for real while calibrating this, as a
        2pp 'effect' from a change that had been reverted to itself."""
        a = deep_season.run_deep(season, n_seasons=per, seed=sd,
                                 profiles=profiles, track_progress=False)
        return a if (a.get("n") or 0) == per else None

    def ws(agg, tid):
        return 100.0 * agg["ws"].get(tid, 0) / (agg["n"] or 1)

    seeds = [seed + b * 7919 for b in range(batches)]
    _say(f"baseline {batches}x{per} seasons")
    base = [_run(s, cur_profiles) for s in seeds]

    priced = 0
    for _, tid, ev in picked:
        cur_team, prev_team = cur_profiles.get(tid), (prev_profiles or {}).get(tid)
        if not cur_team or not prev_team:
            ev["delta_note"] = "no comparable roster to revert against"
            continue
        cf = dict(cur_profiles)
        cf[tid] = _revert_player(cur_team, prev_team, ev["pid"])
        # Reverting must actually change something, or we would burn a run to
        # measure nothing -- e.g. a 60-day IL player the sim already excluded.
        # Compare the ROSTER GROUPS only: a profile also carries incidental keys
        # (a data-quality report, for one), and comparing whole dicts would make
        # every revert look like a change and pay for a counterfactual that
        # measures nothing.
        if all(cf[tid].get(g) == cur_team.get(g) for g in _GROUPS):
            ev["delta_note"] = "no effect on the simulated roster"
            continue
        ds = []
        for b, s in enumerate(seeds):
            if base[b] is None:
                continue
            agg = _run(s, cf)
            if agg is None:
                continue
            ds.append(ws(base[b], tid) - ws(agg, tid))
        if len(ds) < 2:
            ev["delta_note"] = "not enough complete runs to measure"
            _say(f"{ev.get('name')}: only {len(ds)} usable slice(s), skipped")
            continue
        mean = sum(ds) / len(ds)
        var = sum((x - mean) ** 2 for x in ds) / (len(ds) - 1)
        se = (var / len(ds)) ** 0.5
        ev["delta_pp"] = round(mean, 2)
        ev["delta_se"] = round(se, 2)
        ev["measured_on"] = per * len(ds)
        ev["batches"] = len(ds)
        # With few slices the SD is itself uncertain, so the bar is a t critical
        # value rather than 2 sigma.
        if abs(mean) <= _t_crit(len(ds)) * se:
            ev["delta_note"] = "no measurable effect"
        priced += 1
        _say(f"{ev.get('name')} ({ev.get('kind')}) -> {mean:+.2f} ± {se:.2f}pp "
             f"over {len(ds)} slices")
    return {"priced": priced, "seasons": seasons, "batches": batches,
            "per_batch": per,
            "skipped": max(0, len(flat) - len(picked))}


# -------------------------------------------------------------- narration ----
# Template sentences, deliberately not generated. Each event kind has one form,
# filled from the event, so the wording is stable and reviewable.

def _pp(ev):
    """The measured effect, with its uncertainty. A counterfactual on a few
    hundred paired seasons resolves about half a point, so the error bar is shown
    rather than implied -- '+4.1 ± 0.5pp' is a claim a reader can weigh, '+4.1pp'
    pretends to a precision the run does not have."""
    d = ev.get("delta_pp")
    if d is None:
        return ""
    if ev.get("delta_note") == "no measurable effect":
        return "  (no measurable effect)"
    se = ev.get("delta_se")
    return f"  {d:+.1f} ± {se:.1f}pp" if se else f"  {d:+.1f}pp"


def sentence(ev):
    """One event -> one sentence."""
    n, pos = ev.get("name") or "A player", ev.get("pos") or ""
    who = f"{n} ({pos})" if pos else n
    k = ev.get("kind")
    if k == "il_in":
        s = f"{who} moved to the {_il_name(ev.get('to'))}"
    elif k == "il_out":
        s = f"{who} returned from the {_il_name(ev.get('frm'))}"
    elif k == "called_up":
        s = f"{who} was called up"
    elif k == "optioned":
        s = f"{who} was optioned to the minors"
    elif k == "added":
        s = f"{who} was added to the roster"
    elif k == "removed":
        s = f"{who} came off the 40-man"
    elif k == "blowup":
        era = ""
        if ev.get("era_from") is not None and ev.get("era_to") is not None:
            era = f"; season ERA {ev['era_from']:.2f} → {ev['era_to']:.2f}"
        ip = ev.get("ip") or 0
        s = (f"{who} allowed {ev.get('runs')} runs in "
             f"{ip:.1f} IP{era}")
    else:
        s = f"{who} status {ev.get('frm')} → {ev.get('to')}"
    return s + _pp(ev)


def _run_noise(n, p=0.15, sigmas=3.0):
    """How far two INDEPENDENT runs of n seasons can disagree on a team's odds
    before it means anything. The nightly runs are not seeded alike, so a small
    unexplained move is usually this and should be named as such."""
    n = n or 1
    return round(sigmas * (p * (1 - p) / n) ** 0.5 * 100 * (2 ** 0.5), 2)


def record_delta(prev_t, cur_t):
    """(wins, losses) a team added between two snapshots, or None if it sat idle."""
    if not prev_t or not cur_t:
        return None
    dw = (cur_t.get("wins") or 0) - (prev_t.get("wins") or 0)
    dl = (cur_t.get("losses") or 0) - (prev_t.get("losses") or 0)
    return None if (dw == 0 and dl == 0) else (dw, dl)


def record_sentence(dw, dl, span=None):
    """'Went 3-4' over one night, or over a window when `span` is (from, to).

    A week-long window used to print seven of these, one per night, each saying
    a team went 0-1 or 1-0. Seven lines is not seven pieces of news -- it is one
    record, chopped up, and the reader has to add it back up to learn anything."""
    if span:
        return f"Went {dw}-{dl} from {span[0]} to {span[1]}"
    return f"Went {dw}-{dl} since the previous run"


def team_sentence(prev_t, cur_t):
    """The mechanical part: they played games. Separated from roster news because
    a team winning is not a surprise and should not read like one."""
    d = record_delta(prev_t, cur_t)
    return record_sentence(d[0], d[1]) if d else None


# ---------------------------------------------------------------- assembly ---

def build_day(agg, season, cur_profiles, attribute_events=True, log=None):
    """Snapshot this run, diff it against the previous one, price the changes and
    persist the result. Returns the day record.

    Safe to call when there is no previous snapshot: the day is stored with no
    events, which is what the first night looks like."""
    rec = snapshot(agg, season)
    prev_date = previous_date(rec["date"])
    prev = load_day(prev_date) if prev_date else None
    rec["prev_date"] = prev_date

    if prev:
        evs = _events(prev, rec)
        moves = {}
        for tid, t in rec["teams"].items():
            pt = (prev.get("teams") or {}).get(tid)
            if pt:
                moves[tid] = round(t["ws"] - pt["ws"], 2)
        rec["moves"] = moves
        if attribute_events and evs:
            prev_profiles = deep_cache.load(_PREV_PROFILES)[0]
            try:
                rec["attribution"] = attribute(
                    evs, cur_profiles, prev_profiles, season,
                    team_moves=moves, log=log)
            except Exception as e:
                rec["attribution"] = {"error": f"{type(e).__name__}: {e}"}
        rec["events"] = evs
    else:
        rec["events"], rec["moves"] = {}, {}

    save_day(rec)
    # Roll the profiles forward for tomorrow's counterfactuals. Written AFTER the
    # day is saved so a crash mid-attribution cannot leave the pointer ahead of
    # the snapshot it belongs to.
    try:
        deep_cache.save(_PREV_PROFILES, cur_profiles)
    except Exception:
        pass
    return rec


# ------------------------------------------------- durable copy in the repo ---
# The app runs on a host with no persistent disk: the deep cache is wiped on every
# restart and redeploy, so a calendar kept only on that disk quietly empties
# itself. The one thing that does survive is the GitHub repo, so the history is
# mirrored there as plain JSON.
#
# The app never writes to GitHub and holds no token. A scheduled Action PULLS
# `export_bundle()` from the running app and commits it -- the same direction the
# existing weekly-sim workflow already works in, using the Action's own
# GITHUB_TOKEN. The app only ever reads back, and since the repo is public that
# needs no credentials either.
#
# It commits to a SEPARATE branch. The repo's default branch is the one Render
# auto-deploys from, so nightly commits there would redeploy the app every night
# -- restarting it, wiping the very cache this exists to protect, and possibly
# killing a running sim.
GH_OWNER = os.environ.get("VIGIL_GH_OWNER", "mmfoodchicks")
GH_REPO = os.environ.get("VIGIL_GH_REPO", "kalshi-betting-helper")
GH_BRANCH = os.environ.get("VIGIL_GH_HISTORY_BRANCH", "sim-history")
GH_DIR = "history/mlb"
_RAW = "https://raw.githubusercontent.com"


def _slim(rec, keep_roster):
    """A day record for the repo. The roster fingerprint is dropped from every
    day but the newest: it exists only to diff the NEXT run against, so keeping
    one per day would commit ~100KB of dead weight every night forever."""
    out = {k: v for k, v in rec.items() if k != "roster"}
    if keep_roster:
        out["roster"] = rec.get("roster") or {}
    return out


def export_bundle(limit=KEEP_DAYS):
    """Everything worth persisting, as plain JSON-able data."""
    ds = dates()[:limit]
    days = []
    for i, d in enumerate(ds):
        rec = load_day(d)
        if rec:
            days.append(_slim(rec, keep_roster=(i == 0)))
    return {"format": 1, "sport": "mlb", "exported_at": time.time(),
            "dates": [d["date"] for d in days], "days": days}


def import_bundle(bundle, overwrite=False):
    """Load an exported bundle back into the local cache. Existing days are kept
    unless `overwrite`, so a restore can never clobber a fresher local run."""
    if not bundle or not isinstance(bundle, dict):
        return {"loaded": 0, "skipped": 0, "error": "not a bundle"}
    have = set(dates())
    loaded = skipped = 0
    for rec in bundle.get("days") or []:
        d = rec.get("date")
        if not d:
            continue
        if d in have and not overwrite:
            skipped += 1
            continue
        deep_cache.save(_key(d), rec)
        loaded += 1
    prune()
    return {"loaded": loaded, "skipped": skipped}


def _raw_url(path):
    return f"{_RAW}/{GH_OWNER}/{GH_REPO}/{GH_BRANCH}/{path}"


def restore_from_github(timeout=12, overwrite=False):
    """Repopulate the local history from the repo copy. Called on boot, so a
    restarted host comes back with its calendar intact.

    Best-effort and silent on failure: no history is a degraded feature, not a
    broken app, and this must never delay or break startup."""
    import json
    import urllib.request
    try:
        req = urllib.request.Request(_raw_url(f"{GH_DIR}/bundle.json"),
                                     headers={"User-Agent": "vigil/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            bundle = json.loads(r.read())
    except Exception as e:
        return {"loaded": 0, "skipped": 0, "error": f"{type(e).__name__}"}
    return import_bundle(bundle, overwrite=overwrite)


def report(date=None):
    """The 'what happened' payload for one day: the teams that moved, each with
    its sentences. `date` defaults to the newest snapshot.

    Every day describes the change from the PREVIOUS snapshot to itself, so the
    current day is answerable the moment its run lands."""
    ds = dates()
    if not ds:
        return None
    date = date or ds[0]
    rec = load_day(date)
    if not rec:
        return None
    prev = load_day(rec.get("prev_date")) if rec.get("prev_date") else None
    evs = rec.get("events") or {}
    moves = rec.get("moves") or {}

    teams = []
    for tid, t in (rec.get("teams") or {}).items():
        pt = (prev.get("teams") or {}).get(tid) if prev else None
        lines = [sentence(e) for e in sorted(
            evs.get(tid) or [],
            key=lambda e: -(abs(e.get("delta_pp") or 0)))]
        gs = team_sentence(pt, t)
        if gs:
            lines.append(gs)
        mv = moves.get(tid)
        if not lines and not mv:
            continue
        # A move with nothing behind it is usually just two independent 4,000-season
        # runs disagreeing. Say so rather than leaving a number unexplained -- and
        # only call it noise when it is actually within the sim's own spread.
        if not lines and mv:
            lines.append(
                f"No roster or form changes found; {abs(mv):.1f}pp is "
                f"{'within' if abs(mv) <= _run_noise(rec.get('n')) else 'ABOVE'} "
                f"the run-to-run spread of a {(rec.get('n') or 0):,}-season sim")
        # The W-L carried STRUCTURED as well as written out, so a multi-day
        # window can add records up without parsing English back out of a
        # sentence it just generated.
        rd = record_delta(pt, t)
        teams.append({
            "id": tid, "name": t.get("name"),
            "ws": t.get("ws"), "ws_prev": pt.get("ws") if pt else None,
            "move": moves.get(tid),
            "playoffs": t.get("playoffs"),
            "playoffs_prev": pt.get("playoffs") if pt else None,
            "mean_wins": t.get("mean_wins"),
            "wins": t.get("wins"), "losses": t.get("losses"),
            "wins_prev": pt.get("wins") if pt else None,
            "losses_prev": pt.get("losses") if pt else None,
            "record": {"w": rd[0], "l": rd[1]} if rd else None,
            "what": lines,
            "events": evs.get(tid) or [],
        })
    # Biggest movers first; teams with news but no move still appear below them.
    teams.sort(key=lambda t: -abs(t.get("move") or 0))
    return {"date": date, "prev_date": rec.get("prev_date"), "days": 1,
            "from": rec.get("prev_date"), "to": date,
            "season": rec.get("season"), "n": rec.get("n"),
            "generated_at": rec.get("generated_at"),
            "n_games_left": rec.get("n_games_left"),
            "attribution": rec.get("attribution"),
            # Run-to-run spread of two INDEPENDENT nightly runs -- what a move
            # with no cause behind it is measured against. Not the attribution
            # error bar, which is per event and travels with each figure.
            "run_noise": _run_noise(rec.get("n")),
            "dates": ds, "teams": teams}


def report_range(start=None, end=None):
    """One combined 'what happened' box spanning every stored run from `start`
    to `end`, inclusive of both endpoints' changes.

    A single day answers "what moved since last night". A week answers "what
    moved since last week", which is a different and usually more useful
    question -- and reading seven separate boxes to assemble it by hand is how
    you miss that a team gained three points on Monday and gave back four on
    Thursday.

    Per team the events from every day in the window are concatenated (newest
    first, biggest first within a day), while the MOVE is measured end to end
    from the oldest day's starting probability to the newest day's -- not summed
    from the dailies, because summing accumulates each night's rounding and, more
    importantly, hides a round trip: +3 then -4 is a -1 week, not "two moves".

    Falls back to a single day when the window holds one run, so callers can use
    this for both and the UI has one shape to render."""
    ds = dates()                                    # newest first
    if not ds:
        return None
    end = end or ds[0]
    start = start or end
    if start > end:
        start, end = end, start
    window = [d for d in ds if start <= d <= end]   # newest first
    if not window:
        return None
    if len(window) == 1:
        return report(window[0])

    newest, oldest = window[0], window[-1]
    per_day = []
    for d in window:                                # newest -> oldest
        r = report(d)
        if r:
            per_day.append(r)
    if not per_day:
        return None
    if len(per_day) == 1:
        return per_day[0]

    base = per_day[0]                               # newest run: current levels
    first = per_day[-1]
    # Where the window STARTS for each team: the earliest day it appears on, not
    # the earliest day in the window. A team only shows up on a day it moved or
    # had news, so taking the oldest day's roster alone left every team absent
    # from it with no baseline -- and therefore no move at all, which on a live
    # board means the teams with a single big piece of news are exactly the ones
    # that come back blank.
    start_ws, start_po, start_w, start_l = {}, {}, {}, {}
    for r in reversed(per_day):                     # oldest -> newest
        for t in r["teams"]:
            start_ws.setdefault(t["id"], t.get("ws_prev"))
            start_po.setdefault(t["id"], t.get("playoffs_prev"))
            start_w.setdefault(t["id"], t.get("wins_prev"))
            start_l.setdefault(t["id"], t.get("losses_prev"))

    merged = {}
    for r in per_day:                               # newest first
        for t in r["teams"]:
            m = merged.setdefault(t["id"], {
                "id": t["id"], "name": t.get("name"),
                "ws": None, "ws_prev": None, "playoffs": None,
                "playoffs_prev": None, "mean_wins": None,
                "wins": None, "losses": None, "record": None,
                "what": [], "events": [], "_days": 0})
            if m["ws"] is None:                     # newest run wins the levels
                m["ws"] = t.get("ws")
                m["playoffs"] = t.get("playoffs")
                m["mean_wins"] = t.get("mean_wins")
            if m["wins"] is None:
                m["wins"], m["losses"] = t.get("wins"), t.get("losses")
            # That night's own W-L line is dropped here and replaced by ONE line
            # for the whole window below. Rebuilt from the structured record and
            # matched exactly, so nothing is parsed out of prose.
            skip = None
            rd = t.get("record")
            if rd:
                skip = record_sentence(rd["w"], rd["l"])
            for line in (t.get("what") or []):
                if line == skip:
                    continue
                # Same sentence can recur across nights (a signing keeps showing
                # up until it ages out); keep the first, newest, occurrence.
                if line not in m["what"]:
                    m["what"].append(f"{r['date']}: {line}")
            m["events"].extend(t.get("events") or [])
            m["_days"] += 1

    span = ((first.get("prev_date") or oldest), newest)
    teams = []
    for tid, m in merged.items():
        sw, sp = start_ws.get(tid), start_po.get(tid)
        m["ws_prev"], m["playoffs_prev"] = sw, sp
        m["move"] = (round(m["ws"] - sw, 1)
                     if (m["ws"] is not None and sw is not None) else None)
        # The window's record, measured END TO END for the same reason the WS
        # move is: a day missing from storage makes a sum of the nightlies quietly
        # short, while first-to-last stays exact.
        w0, l0 = start_w.get(tid), start_l.get(tid)
        if m["wins"] is not None and w0 is not None:
            dw, dl = m["wins"] - w0, (m["losses"] or 0) - (l0 or 0)
            if dw or dl:
                m["record"] = {"w": dw, "l": dl, "from": span[0], "to": span[1]}
                m["what"].insert(0, record_sentence(dw, dl, span))
        m["days_seen"] = m.pop("_days")
        if m["what"] or m["move"]:
            teams.append(m)
    teams.sort(key=lambda t: -abs(t.get("move") or 0))
    return {"date": newest, "prev_date": first.get("prev_date"),
            "from": first.get("prev_date") or oldest, "to": newest,
            "days": len(per_day), "range": True,
            "season": base.get("season"), "n": base.get("n"),
            "generated_at": base.get("generated_at"),
            "n_games_left": base.get("n_games_left"),
            "attribution": base.get("attribution"),
            "run_noise": _run_noise(base.get("n")),
            "dates": ds, "teams": teams}
