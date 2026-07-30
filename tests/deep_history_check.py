"""Invariants for the deep-sim day-over-day history.

Everything here runs OFFLINE on synthetic fixtures. That is deliberate: the parts
worth protecting are the diff, the revert and the wording, and all three are pure
functions of two snapshots. The expensive counterfactual machinery is exercised
separately with --live.

The failure mode this guards against is the dangerous one: a roster diff that
silently produces a plausible sentence with the wrong player, the wrong direction,
or a number attached to a change that was never actually measured.

Run: python3 tests/deep_history_check.py          (add --live for a real run)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

PASS, FAIL = [], []


def ck(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))


import deep_history as dh

print("=" * 74)
print("1. Innings parsing (MLB writes thirds as decimals)")
print("=" * 74)
ck("'99.1' is 99 and one third", abs(dh._ip("99.1") - 99.3333) < 0.001, dh._ip("99.1"))
ck("'99.2' is 99 and two thirds", abs(dh._ip("99.2") - 99.6667) < 0.001, dh._ip("99.2"))
ck("'100.0' is exact", dh._ip("100.0") == 100.0)
ck("None is zero, not a crash", dh._ip(None) == 0.0)
ck("garbage is zero, not a crash", dh._ip("abc") == 0.0)
ck("a one-inning outing differences correctly",
   abs((dh._ip("34.0") - dh._ip("33.0")) - 1.0) < 1e-9)

print()
print("=" * 74)
print("2. Event detection")
print("=" * 74)


def snap(roster, teams=None):
    return {"roster": {"1": roster},
            "teams": teams or {"1": {"name": "Team", "wins": 50, "losses": 40,
                                     "ws": 10.0}}}


def P(**kw):
    base = {"name": "Guy", "pos": "P", "status": "A", "ip": 40.0, "er": 15,
            "era": 3.38, "gs": 0}
    base.update(kw)
    return base


prev = snap({10: P(name="Closer"), 11: P(name="Starter", gs=20),
             12: P(name="Hurt", status="D15"), 13: P(name="Minors", status="RM"),
             14: P(name="Gone")})
cur = snap({10: P(name="Closer", status="D60"),          # -> 60-day IL
            11: P(name="Starter", gs=21, ip=41.0, er=26, era=5.71),  # blowup
            12: P(name="Hurt", status="A"),               # <- off the IL
            13: P(name="Minors", status="A"),             # called up
            15: P(name="NewGuy")})                        # added; 14 removed

evs = dh._events(prev, cur)["1"]
kinds = {e["name"]: e["kind"] for e in evs}
ck("player onto the IL is il_in", kinds.get("Closer") == "il_in", kinds)
ck("player off the IL is il_out", kinds.get("Hurt") == "il_out")
ck("RM -> A is a call-up", kinds.get("Minors") == "called_up")
ck("a new pid is 'added'", kinds.get("NewGuy") == "added")
ck("a vanished active pid is 'removed'", kinds.get("Gone") == "removed")
blow = [e for e in evs if e["kind"] == "blowup"]
ck("11 runs in an inning is a blowup", len(blow) == 1 and blow[0]["name"] == "Starter",
   blow[:1])
if blow:
    ck("blowup carries the runs and innings it is claiming",
       blow[0]["runs"] == 11 and abs(blow[0]["ip"] - 1.0) < 1e-9,
       (blow[0]["runs"], blow[0]["ip"]))
ck("the IL move records where it went",
   next((e for e in evs if e["kind"] == "il_in"), {}).get("to") == "D60")

# a quiet day must produce NO events at all
ck("an unchanged roster yields no events", not dh._events(prev, prev).get("1"))

# a normal bad start must not trip the blowup rule
ok = snap({11: P(name="Starter", gs=21, ip=46.0, er=20, era=4.10)})
ck("a 6 IP / 5 ER start is not a blowup",
   not [e for e in (dh._events(snap({11: P(name="Starter", gs=20)}), ok).get("1") or [])
        if e["kind"] == "blowup"])

# a team we have never seen before cannot generate events
ck("an unseen team is skipped, not diffed against nothing",
   "1" not in dh._events({"roster": {}}, cur))

print()
print("=" * 74)
print("3. Reverting a player for the counterfactual")
print("=" * 74)
cur_team = {"rotation": [{"id": 1}, {"id": 2}, {"id": 3}], "bullpen": [{"id": 9}],
            "depth": [], "lineup": [], "bench": [], "depth_bats": []}
prev_team = {"rotation": [{"id": 1}, {"id": 7, "era": 2.0}, {"id": 2}, {"id": 3}],
             "bullpen": [{"id": 9}], "depth": [], "lineup": [], "bench": [],
             "depth_bats": []}
rv = dh._revert_player(cur_team, prev_team, 7)
ck("a reverted player is restored", [p["id"] for p in rv["rotation"]] == [1, 7, 2, 3],
   [p["id"] for p in rv["rotation"]])
ck("restored in the SLOT they held (rotation order decides starts)",
   rv["rotation"][1]["id"] == 7)
ck("other groups untouched", rv["bullpen"] == [{"id": 9}])

# removing someone who is present today but absent yesterday
rv2 = dh._revert_player(cur_team, {"rotation": [{"id": 1}, {"id": 3}]}, 2)
ck("a player absent yesterday is removed from the counterfactual",
   [p["id"] for p in rv2["rotation"]] == [1, 3], [p["id"] for p in rv2["rotation"]])
ck("reverting an unknown player is a no-op, not a crash",
   dh._revert_player(cur_team, prev_team, 9999)["rotation"]
   == cur_team["rotation"])

print()
print("=" * 74)
print("4. Sentences")
print("=" * 74)
cases = [
    ({"kind": "il_out", "name": "Treinen", "pos": "P", "frm": "D15",
      "delta_pp": 4.2}, ["Treinen", "returned", "15-day IL", "+4.2pp"]),
    ({"kind": "il_in", "name": "Betts", "pos": "RF", "to": "D10",
      "delta_pp": -3.1}, ["Betts", "moved to", "10-day IL", "-3.1pp"]),
    ({"kind": "blowup", "name": "Thomas", "pos": "P", "runs": 11, "ip": 1.0,
      "era_from": 2.90, "era_to": 4.85, "delta_pp": -1.4},
     ["Thomas", "11 runs", "1.0 IP", "2.90", "4.85", "-1.4pp"]),
    ({"kind": "called_up", "name": "Rook", "pos": "SS"}, ["Rook", "called up"]),
    ({"kind": "removed", "name": "Vet", "pos": "1B"}, ["Vet", "40-man"]),
]
for ev, want in cases:
    s = dh.sentence(ev)
    miss = [w for w in want if w not in s]
    ck(f"{ev['kind']}: {s[:56]}", not miss, f"missing {miss}" if miss else "")

ck("an unpriced event shows no number",
   "pp" not in dh.sentence({"kind": "il_in", "name": "X", "to": "D10"}))
ck("a below-noise delta says so instead of printing a fake number",
   "no measurable effect" in dh.sentence(
       {"kind": "il_in", "name": "X", "to": "D10", "delta_pp": 0.05,
        "delta_note": "no measurable effect"}))
ck("a priced figure carries its error bar",
   "+4.1 \u00b1 0.5pp" in dh.sentence(
       {"kind": "il_out", "name": "T", "pos": "P", "frm": "D15",
        "delta_pp": 4.1, "delta_se": 0.54}),
   dh.sentence({"kind": "il_out", "name": "T", "pos": "P", "frm": "D15",
                "delta_pp": 4.1, "delta_se": 0.54}))
ck("a below-noise delta does NOT also print the number",
   "+0.1pp" not in dh.sentence(
       {"kind": "il_in", "name": "X", "to": "D10", "delta_pp": 0.05,
        "delta_note": "no measurable effect"}))
ck("every event kind renders a non-empty string",
   all(dh.sentence({"kind": k, "name": "N", "pos": "P"}).strip()
       for k in ("il_in", "il_out", "called_up", "optioned", "added",
                 "removed", "blowup", "status", "nonsense")))
ck("games-played sentence reads as a record",
   dh.team_sentence({"wins": 50, "losses": 40}, {"wins": 52, "losses": 40})
   == "Went 2-0 since the previous run")
ck("no games played -> no sentence",
   dh.team_sentence({"wins": 50, "losses": 40}, {"wins": 50, "losses": 40}) is None)
ck("missing previous team -> no sentence", dh.team_sentence(None, {"wins": 1}) is None)

print()
print("=" * 74)
print("5. Storage, ordering and pruning")
print("=" * 74)
import tempfile
import deep_cache
_orig = deep_cache.CACHE_DIR
deep_cache.CACHE_DIR = tempfile.mkdtemp(prefix="dhtest_")
try:
    ck("no snapshots -> empty list, not an error", dh.dates() == [])
    ck("report with no snapshots returns None", dh.report() is None)
    for d in ("2026-07-27", "2026-07-28", "2026-07-29"):
        dh.save_day({"date": d, "teams": {}, "roster": {}, "events": {}, "moves": {}})
    ck("dates come back newest first", dh.dates()[0] == "2026-07-29", dh.dates())
    ck("previous_date walks backwards",
       dh.previous_date("2026-07-29") == "2026-07-28")
    ck("previous_date of the oldest is None",
       dh.previous_date("2026-07-27") is None)
    ck("previous_date ignores same-day", dh.previous_date("2026-07-28") == "2026-07-27")
    ck("a saved day round-trips", dh.load_day("2026-07-28")["date"] == "2026-07-28")
    ck("an absent day is None, not a crash", dh.load_day("1999-01-01") is None)
    dh.prune(keep=2)
    ck("prune keeps the newest N", dh.dates() == ["2026-07-29", "2026-07-28"], dh.dates())

    # report assembly, including a team with news but no move
    dh.save_day({"date": "2026-07-30", "prev_date": "2026-07-29",
                 "season": "2026", "n": 4000,
                 "teams": {"1": {"name": "A", "ws": 18.1, "playoffs": 90.0,
                                 "wins": 60, "losses": 45, "mean_wins": 95.0},
                           "2": {"name": "B", "ws": 5.0, "playoffs": 40.0,
                                 "wins": 50, "losses": 55, "mean_wins": 80.0}},
                 "roster": {},
                 "moves": {"1": 5.7, "2": 0.0},
                 "events": {"1": [{"kind": "il_out", "name": "Treinen", "pos": "P",
                                   "frm": "D15", "delta_pp": 4.1}],
                            "2": [{"kind": "blowup", "name": "Thomas", "pos": "P",
                                   "runs": 11, "ip": 1.0}]}})
    dh.save_day({"date": "2026-07-29", "teams": {
        "1": {"name": "A", "ws": 12.4, "playoffs": 86.0, "wins": 58, "losses": 44},
        "2": {"name": "B", "ws": 5.0, "playoffs": 40.0, "wins": 50, "losses": 55}},
        "roster": {}, "events": {}, "moves": {}})
    rep = dh.report("2026-07-30")
    ck("report builds", bool(rep) and rep["date"] == "2026-07-30")
    ck("biggest mover is first", rep["teams"][0]["name"] == "A",
       [t["name"] for t in rep["teams"]])
    ck("mover carries both the old and the new number",
       rep["teams"][0]["ws"] == 18.1 and rep["teams"][0]["ws_prev"] == 12.4)
    ck("the mover's sentence is present and priced",
       "Treinen" in rep["teams"][0]["what"][0] and "+4.1pp" in rep["teams"][0]["what"][0],
       rep["teams"][0]["what"][:1])
    ck("games-played line is appended after the roster news",
       any("Went 2-1" in s for s in rep["teams"][0]["what"]),
       rep["teams"][0]["what"])
    ck("a team with news but no move still appears",
       any(t["name"] == "B" for t in rep["teams"]))
    ck("report lists the available dates for a calendar",
       "2026-07-30" in rep["dates"] and "2026-07-29" in rep["dates"])
    ck("report exposes the noise floor it used", rep["noise_floor"] == dh.noise_floor())
    ck("defaulting to the newest date works", dh.report()["date"] == "2026-07-30")

    # A move with nothing behind it must be NAMED, not left as a bare number.
    dh.save_day({"date": "2026-08-01", "prev_date": "2026-07-30", "n": 4000,
                 "teams": {"9": {"name": "Quiet", "ws": 10.5, "playoffs": 50.0,
                                 "wins": 50, "losses": 50},
                           "8": {"name": "Swung", "ws": 30.0, "playoffs": 70.0,
                                 "wins": 50, "losses": 50}},
                 "roster": {}, "events": {},
                 "moves": {"9": 0.4, "8": 9.0}})
    dh.save_day({"date": "2026-07-30", "teams": {
        "9": {"name": "Quiet", "ws": 10.1, "wins": 50, "losses": 50},
        "8": {"name": "Swung", "ws": 21.0, "wins": 50, "losses": 50}},
        "roster": {}, "events": {}, "moves": {}})
    r2 = {t["name"]: t for t in dh.report("2026-08-01")["teams"]}
    ck("a small unexplained move is called run-to-run noise",
       any("within" in s for s in r2["Quiet"]["what"]), r2["Quiet"]["what"])
    ck("a LARGE unexplained move is flagged as above the noise, not excused",
       any("ABOVE" in s for s in r2["Swung"]["what"]), r2["Swung"]["what"])
    ck("the noise bound shrinks as seasons rise",
       dh._run_noise(16000) < dh._run_noise(1000))
finally:
    import shutil
    shutil.rmtree(deep_cache.CACHE_DIR, ignore_errors=True)
    deep_cache.CACHE_DIR = _orig

print()
print("=" * 74)
print("6. Export / restore (the repo is the only durable storage)")
print("=" * 74)
import json as _json
import shutil as _shutil
import tempfile as _tf

import deep_cache as _dc2

_od = _dc2.CACHE_DIR
_dc2.CACHE_DIR = _tf.mkdtemp(prefix="dhx_")
try:
    big = {str(i): {j: {"name": f"P{j}", "pos": "P", "status": "A",
                        "ip": 10.0, "er": 4, "era": 3.6} for j in range(40)}
           for i in range(30)}
    dh.save_day({"date": "2026-07-28", "teams": {"1": {"name": "A", "ws": 5.0}},
                 "roster": big, "events": {}, "moves": {}})
    dh.save_day({"date": "2026-07-29", "teams": {"1": {"name": "A", "ws": 6.0}},
                 "roster": big, "events": {}, "moves": {"1": 1.0}})
    dh.save_day({"date": "2026-07-30", "teams": {"1": {"name": "A", "ws": 7.0}},
                 "roster": big, "events": {}, "moves": {"1": 1.0}})
    ck("only the newest day keeps a roster fingerprint",
       dh.load_day("2026-07-30").get("roster")
       and not dh.load_day("2026-07-29").get("roster"),
       [bool(dh.load_day(d).get("roster")) for d in dh.dates()])

    b = dh.export_bundle()
    ck("bundle is JSON-serialisable (it is committed as text)",
       isinstance(_json.dumps(b), str))
    ck("bundle carries every stored day", len(b["days"]) == 3, len(b["days"]))
    ck("bundle keeps exactly one roster (the newest)",
       sum(1 for d in b["days"] if d.get("roster")) == 1)
    ck("the retained roster is the newest day's",
       (b["days"][0].get("date") == "2026-07-30") and bool(b["days"][0].get("roster")))
    ck("bundle is versioned so a future format can be detected", b.get("format") == 1)

    # a restore into an empty host
    _shutil.rmtree(_dc2.CACHE_DIR, ignore_errors=True)
    _dc2.CACHE_DIR = _tf.mkdtemp(prefix="dhx2_")
    ck("host starts with nothing", dh.dates() == [])
    res = dh.import_bundle(b)
    ck("restore loads every day", res["loaded"] == 3, res)
    ck("restored dates match", dh.dates() == ["2026-07-30", "2026-07-29", "2026-07-28"],
       dh.dates())
    ck("restored content survives the round trip",
       dh.load_day("2026-07-29")["teams"]["1"]["ws"] == 6.0)
    ck("the newest roster survives, so the next run can still diff",
       bool(dh.load_day("2026-07-30").get("roster")))

    # a restore must never clobber a fresher local run
    dh.save_day({"date": "2026-07-30", "teams": {"1": {"name": "A", "ws": 99.0}},
                 "roster": {}, "events": {}, "moves": {}})
    res2 = dh.import_bundle(b)
    ck("restore skips days already present", res2["skipped"] >= 1, res2)
    ck("a newer local day is NOT overwritten by an older repo copy",
       dh.load_day("2026-07-30")["teams"]["1"]["ws"] == 99.0)
    dh.import_bundle(b, overwrite=True)
    ck("overwrite=True does replace it",
       dh.load_day("2026-07-30")["teams"]["1"]["ws"] == 7.0)

    ck("garbage bundle is refused, not imported",
       dh.import_bundle({"nope": 1})["loaded"] == 0)
    ck("None bundle is refused", dh.import_bundle(None).get("error"))
    ck("a day with no date is skipped rather than saved under None",
       dh.import_bundle({"days": [{"teams": {}}]}, overwrite=True)["loaded"] == 0)
finally:
    _shutil.rmtree(_dc2.CACHE_DIR, ignore_errors=True)
    _dc2.CACHE_DIR = _od

ck("restore points at a branch that is NOT the deploy branch",
   dh.GH_BRANCH != "claude/kalshi-crypto-predictor-ckutwm", dh.GH_BRANCH)
ck("the raw URL is well formed",
   dh._raw_url("history/mlb/bundle.json").startswith(
       "https://raw.githubusercontent.com/") and dh.GH_BRANCH in dh._raw_url("x"))
ck("an unreachable repo degrades to no history, not an exception",
   isinstance(dh.restore_from_github(timeout=0.001), dict))

print()
print("=" * 74)
print("7. Attribution budget and honesty")
print("=" * 74)
ck("attribution is capped", dh._MAX_ATTRIB <= 16, dh._MAX_ATTRIB)
ck("a noise floor is set and non-trivial", dh.noise_floor() > 0)
ck("the noise floor is twice the standard error at the seasons used",
   abs(dh.noise_floor(500) - 2 * dh.attrib_se(500)) < 0.01,
   f"SE {dh.attrib_se(500):.2f} floor {dh.noise_floor(500):.2f}")
ck("more paired seasons -> a tighter standard error",
   dh.attrib_se(4000) < dh.attrib_se(500) < dh.attrib_se(80))
ck("the SE matches what was measured at n=150 (~1.6pp)",
   1.4 < dh.attrib_se(150) < 1.9, round(dh.attrib_se(150), 2))
ck("the shipped variance is the CONSERVATIVE of the two measurements",
   dh._ATTRIB_VAR >= 0.038 - 1e-9, dh._ATTRIB_VAR)
ck("roster moves outrank form for the budget",
   dh._impact_rank({"kind": "il_out", "pos": "P"}, 5.0)
   > dh._impact_rank({"kind": "blowup", "pos": "P"}, 5.0))
ck("a big team move raises an event's priority",
   dh._impact_rank({"kind": "il_out", "pos": "P"}, 8.0)
   > dh._impact_rank({"kind": "il_out", "pos": "P"}, 0.0))
ck("no events -> no baseline run is paid for",
   dh.attribute({}, {}, {}, "2026")["priced"] == 0)

# A counterfactual is only paired if BOTH sides ran the same seasons. A short run
# divides by a different denominator, which manufactures an effect out of nothing:
# while measuring this, a run that came back short produced a 2pp "effect" from a
# change that had been reverted to itself. Stub the sim to force that case.
import deep_season as _ds

_orig_run = _ds.run_deep
_calls = {"i": 0}


def _fake_run(season, n_seasons=None, seed=None, profiles=None, **kw):
    _calls["i"] += 1
    # baseline full length; the counterfactual comes back three seasons short
    n = n_seasons if _calls["i"] == 1 else n_seasons - 3
    return {"n": n, "ws": {"1": int(n * 0.30)}, "meta": {}}


_ds.run_deep = _fake_run
try:
    evs = {"1": [{"pid": 7, "name": "Arm", "pos": "P", "kind": "il_in", "to": "D15"}]}
    cur = {"1": {g: ([{"id": 7}] if g == "bullpen" else []) for g in dh._GROUPS}}
    prev = {"1": {g: [] for g in dh._GROUPS}}
    info = dh.attribute(evs, cur, prev, "2026", seasons=100)
    ev = evs["1"][0]
    ck("a short counterfactual is refused, not reported", ev.get("delta_pp") is None,
       ev.get("delta_pp"))
    ck("and it says why", "unpaired run" in (ev.get("delta_note") or ""),
       ev.get("delta_note"))
    ck("a refused event is not counted as priced", info["priced"] == 0, info)
    ck("its sentence carries no number", "pp" not in dh.sentence(ev), dh.sentence(ev))
finally:
    _ds.run_deep = _orig_run

print()
print("=" * 74)
print("7. The engine draws only from the rng it is handed")
print("=" * 74)
# Attribution is a DIFFERENCE between two runs on the same seed, so every draw in
# the engine has to come from that seed. _pick_ph used to roll the pinch-hit
# decision on the module-global `random`, which made the deep sim irreproducible
# and silently reintroduced exactly the noise the pairing exists to cancel. This
# is a source-level guard because the functional version needs live rosters.
import re

for mod in ("deep_sim.py", "season_sim.py", "deep_season.py"):
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", mod)).read()
    # module-level `random.foo(` calls, excluding `rng.`/`_rng.`-qualified ones
    # and the legitimate seeding of a fresh Random in deep_season.
    bad = [m for m in re.findall(r"(?<![\w.])random\.(\w+)\(", src)
           if m not in ("Random", "randrange", "seed")]
    ck(f"{mod} has no unseeded global draws", not bad, sorted(set(bad)))
ck("_pick_ph takes an rng parameter",
   "def _pick_ph(bench, due, pitcher_hand, used, rng)" in
   open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                     "deep_sim.py")).read())

print()
print("=" * 74)
print("8. End to end: two runs in, sentences out")
print("=" * 74)
# The whole pipeline on fabricated runs -- no network, no sim. This is the exact
# pair of situations the feature was asked for: a reliever off the IL, and a
# pitcher whose ERA is wrecked by one inning.
import datetime
import shutil
import tempfile

import clock as _clock
import deep_cache as _dc

_orig_dir, _orig_today = _dc.CACHE_DIR, _clock.today_et
_orig_fp = dh._roster_fingerprint
_dc.CACHE_DIR = tempfile.mkdtemp(prefix="dhe2e_")
try:
    def _agg(ws1, wins1):
        return {"n": 1000, "ws": {"1": ws1, "2": 1000 - ws1},
                "pennant": {"1": 400, "2": 600}, "playoffs": {"1": 900, "2": 500},
                "division": {"1": 300, "2": 200},
                "wins_sum": {"1": wins1 * 1000, "2": 80000}, "n_games_left": 60,
                "meta": {"1": {"name": "Dodgers", "division": "NLW", "league": "NL",
                               "wins": 60, "losses": 45},
                         "2": {"name": "Padres", "division": "NLW", "league": "NL",
                               "wins": 55, "losses": 50}}}

    days = [
        {"1": {10: {"name": "Treinen", "pos": "P", "status": "D15", "ip": 30.0,
                    "er": 10, "era": 3.00}},
         "2": {20: {"name": "Thomas", "pos": "P", "status": "A", "ip": 33.0,
                    "er": 12, "era": 3.27}}},
        {"1": {10: {"name": "Treinen", "pos": "P", "status": "A", "ip": 30.0,
                    "er": 10, "era": 3.00}},
         "2": {20: {"name": "Thomas", "pos": "P", "status": "A", "ip": 34.0,
                    "er": 23, "era": 6.27}}},
    ]
    box = {"i": 0}
    dh._roster_fingerprint = lambda s, t: days[box["i"]]

    _clock.today_et = lambda: datetime.date(2026, 7, 29)
    dh.build_day(_agg(124, 90.0), "2026", {}, attribute_events=False)
    box["i"] = 1
    _clock.today_et = lambda: datetime.date(2026, 7, 30)
    d2 = dh.build_day(_agg(181, 93.0), "2026", {}, attribute_events=False)
    _clock.today_et = _orig_today

    ck("the second day links back to the first", d2.get("prev_date") == "2026-07-29")
    rep = dh.report()
    ck("report defaults to the newest day", rep["date"] == "2026-07-30")
    by = {t["name"]: t for t in rep["teams"]}
    ck("both clubs appear", set(by) == {"Dodgers", "Padres"}, list(by))
    ck("the improving club moves up", round(by["Dodgers"]["move"], 1) == 5.7,
       by["Dodgers"]["move"])
    ck("the declining club moves down", round(by["Padres"]["move"], 1) == -5.7,
       by["Padres"]["move"])
    ck("IL return is reported on the right club",
       any("Treinen" in s and "returned" in s for s in by["Dodgers"]["what"]),
       by["Dodgers"]["what"])
    ck("the blowup is reported on the right club",
       any("Thomas" in s and "11 runs" in s for s in by["Padres"]["what"]),
       by["Padres"]["what"])
    ck("no club is credited with the other's news",
       not any("Thomas" in s for s in by["Dodgers"]["what"]))
    ck("with attribution off, no pp figure is invented",
       not any("pp" in s for t in rep["teams"] for s in t["what"]))
    ck("the first day has no events to report",
       not (dh.load_day("2026-07-29").get("events") or {}))
finally:
    dh._roster_fingerprint = _orig_fp
    _clock.today_et = _orig_today
    shutil.rmtree(_dc.CACHE_DIR, ignore_errors=True)
    _dc.CACHE_DIR = _orig_dir

print()
print("=" * 74)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
for n, d in FAIL:
    print(f"   - {n}   {d}")
print("=" * 74)

if "--live" not in sys.argv:
    print("\nThe counterfactual machinery is UNVERIFIED until this is run with")
    print("--live, which pays for real season runs.")
    sys.exit(1 if FAIL else 0)

# ------------------------------------------------------------------ live -----
print()
print("=" * 74)
print("LIVE: a real paired counterfactual")
print("=" * 74)
import clock
import deep_season
import season_sim

season = str(clock.today_et().year)
stand = season_sim._standings(season)
tids = list(stand)
profs = deep_season._profiles(season, tids)
N = 400
SEED = 4242
base = deep_season.run_deep(season, n_seasons=N, seed=SEED, profiles=profs,
                            track_progress=False)
top = max(stand, key=lambda t: base["ws"].get(t, 0))
victim = sorted(profs[top]["bullpen"], key=lambda p: -(p.get("ip") or 0))[0]
cf_profs = dict(profs)
cf_profs[top] = {g: [p for p in (profs[top].get(g) or []) if p["id"] != victim["id"]]
                 for g in dh._GROUPS}
cf = deep_season.run_deep(season, n_seasons=N, seed=SEED, profiles=cf_profs,
                          track_progress=False)


def ws(a, t):
    return 100.0 * a["ws"].get(t, 0) / (a["n"] or 1)


d = ws(base, top) - ws(cf, top)
print(f"  {stand[top]['name']}: removing {victim['name']} costs {d:+.2f}pp "
      f"({N} paired seasons)")
untouched = sorted(abs(ws(base, t) - ws(cf, t)) for t in tids if t != top)
med = untouched[len(untouched) // 2]
print(f"  untouched teams move a median {med:.2f}pp  <- the real noise floor")
print(f"  shipped _NOISE_FLOOR = {dh._NOISE_FLOOR}")
ck("removing a leverage arm lowers that team's odds", d > 0, f"{d:+.2f}pp")
ck("the shipped noise floor covers the measured drift",
   dh._NOISE_FLOOR >= med, f"measured {med:.2f} vs shipped {dh._NOISE_FLOOR}")
ck("identical inputs on the same seed reproduce exactly",
   deep_season.run_deep(season, n_seasons=40, seed=7, profiles=profs,
                        track_progress=False)["ws"]
   == deep_season.run_deep(season, n_seasons=40, seed=7, profiles=profs,
                           track_progress=False)["ws"])
print(f"\nRESULT: {len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
