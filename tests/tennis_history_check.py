"""Validate the deep tennis history loader.

Two modes.

  (default) OFFLINE. Drives tennis_history.parse over synthetic fixtures: a
  well-formed archive file, files with each required column missing, alternate
  column spellings, junk dates, self-matches, short rows, an HTML error page, and
  a CSV with the right columns in a different ORDER. This proves the parser
  accepts what it should and -- more important -- rejects what it should, since
  the whole safety argument rests on it refusing to guess.

  --live  ONLINE, and the one that actually closes the loop. Fetches the real
  archive files, prints the header each one really has and which columns matched,
  reports rows and date coverage, then re-fits the Elo K factor over the deepened
  pool. The loader was written in a sandbox that cannot reach these repositories,
  so until this passes somewhere that can, the deep source is unverified.

Run:  python3 tests/tennis_history_check.py
      python3 tests/tennis_history_check.py --live
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import tennis_history as th

PASS, FAIL = [], []


def ck(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))


# The layout the loader expects, written out in full so a future reader can see
# exactly what is being assumed.
GOOD = (
    "tourney_id,tourney_name,surface,draw_size,tourney_level,tourney_date,match_num,"
    "winner_id,winner_name,winner_hand,loser_id,loser_name,loser_hand,score,best_of,round\n"
    "2024-001,Melbourne,Hard,128,G,20240115,1,"
    "1001,Novak Djokovic,R,1002,Carlos Alcaraz,R,6-4 6-4,5,R64\n"
    "2024-002,Monte Carlo,Clay,64,M,20240408,3,"
    "1003,Rafael Nadal,L,1004,Jannik Sinner,R,7-5 6-3,3,QF\n"
    "2024-003,Wimbledon,Grass,128,G,20240701,7,"
    "1005,Iga Swiatek,R,1006,Coco Gauff,R,6-2 6-2,3,R32\n"
)


def rows_of(txt):
    r, why = th.parse(txt)
    return r, why


print("=" * 72)
print("1. A well-formed archive file")
print("=" * 72)
rows, why = rows_of(GOOD)
ck("parses without rejection", why is None, why)
ck("all three rows kept", len(rows) == 3, len(rows))
if rows:
    d, w, l, s, lv = rows[0]
    ck("date read straight through", d == "20240115", d)
    ck("winner name", w == "Novak Djokovic", w)
    ck("loser name", l == "Carlos Alcaraz", l)
    ck("surface mapped", s == "Hard", s)
    ck("level captured", lv == "G", lv)
    ck("clay row", rows[1][3] == "Clay", rows[1][3])
    ck("grass row", rows[2][3] == "Grass", rows[2][3])

print()
print("=" * 72)
print("2. Rejection paths — the safety argument")
print("=" * 72)
for miss in ("tourney_date", "winner_name", "loser_name"):
    broken = GOOD.replace(miss + ",", "junk_col,", 1)
    r, why = rows_of(broken)
    ck(f"missing {miss} -> rejected", not r and why, why)
r, why = rows_of("")
ck("empty file -> rejected", not r and why, why)
r, why = rows_of("<!doctype html><html>404</html>")
ck("HTML error page -> rejected", not r and why, why)
r, why = rows_of("a,b,c\n1,2,3\n")
ck("unrelated CSV -> rejected", not r and why, why)

print()
print("=" * 72)
print("3. Tolerances that must NOT reject")
print("=" * 72)
# surface + level are optional
lean = ("tourney_date,winner_name,loser_name\n"
        "20240115,A Player,B Player\n")
r, why = rows_of(lean)
ck("surface/level absent -> still parses", why is None and len(r) == 1, why or len(r))
ck("absent surface is None, not guessed", r and r[0][3] is None, r and r[0][3])
# alternate spellings
alt = ("date,winner,loser,court_surface\n"
       "2024-01-15,A Player,B Player,clay\n")
r, why = rows_of(alt)
ck("alternate column names accepted", why is None and len(r) == 1, why or len(r))
ck("dashed date normalised", r and r[0][0] == "20240115", r and r[0][0])
ck("lowercase surface mapped", r and r[0][3] == "Clay", r and r[0][3])
# column order must not matter
reordered = ("winner_name,tourney_date,surface,loser_name\n"
             "A Player,20240115,Grass,B Player\n")
r, why = rows_of(reordered)
ck("column ORDER irrelevant", why is None and len(r) == 1 and r[0][1] == "A Player",
   why or (r and r[0]))
ck("reordered surface still right", r and r[0][3] == "Grass", r and r[0][3])

print()
print("=" * 72)
print("4. Bad rows are dropped, not fatal")
print("=" * 72)
dirty = ("tourney_date,winner_name,loser_name,surface\n"
         "20240115,Good One,Other Guy,Hard\n"
         "notadate,X,Y,Hard\n"
         "20240116,,Missing Winner,Hard\n"
         "20240117,Same Name,Same Name,Hard\n"
         "20240118,Short Row\n"
         "19000101,Too,Old But Valid,Hard\n"
         "20240119,Carpet Player,Indoor Guy,Carpet\n")
r, why = rows_of(dirty)
ck("file still accepted", why is None, why)
names = [x[1] for x in r]
ck("junk date dropped", "X" not in names, names)
ck("blank winner dropped", "Missing Winner" not in [x[2] for x in r])
ck("self-match dropped", "Same Name" not in names, names)
ck("short row dropped", "Short Row" not in names, names)
ck("good rows survive", "Good One" in names and "Carpet Player" in names, names)
ck("carpet bucketed with hard",
   any(x[3] == "Hard" for x in r if x[1] == "Carpet Player"))
ck("unrecognised surface -> None not a guess",
   th._norm_surface("Astroturf") is None, th._norm_surface("Astroturf"))

print()
print("=" * 72)
print("5. Wiring: disabled + unavailable must be no-ops")
print("=" * 72)
old = os.environ.get("VIGIL_TENNIS_HISTORY")
os.environ["VIGIL_TENNIS_HISTORY"] = "0"
ck("env flag disables", not th.enabled())
ck("disabled -> results() empty", th.results() == [])
os.environ["VIGIL_TENNIS_HISTORY"] = "1"
ck("env flag re-enables", th.enabled())
if old is None:
    os.environ.pop("VIGIL_TENNIS_HISTORY", None)
else:
    os.environ["VIGIL_TENNIS_HISTORY"] = old

import tennis_elo
ck("elo tier constants present",
   tennis_elo._TOUR_START == 1600.0 and tennis_elo._ITF_START == 1400.0)
ck("low-tier codes cover futures + qualifying",
   {"15", "25", "C", "Q"} <= tennis_elo._LOW_TIERS, sorted(tennis_elo._LOW_TIERS))

# The merge must survive the deep source blowing up entirely.
import types
saved = sys.modules.get("tennis_history")
boom = types.ModuleType("tennis_history")
boom.results = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network gone"))
sys.modules["tennis_history"] = boom
try:
    pools = tennis_elo._build()
    ck("_build survives a throwing history source",
       isinstance(pools, dict) and "m" in pools and "w" in pools,
       {k: len(v) for k, v in pools.items()} if isinstance(pools, dict) else pools)
except Exception as e:
    ck("_build survives a throwing history source", False, f"{type(e).__name__}: {e}")
finally:
    if saved is not None:
        sys.modules["tennis_history"] = saved
    else:
        sys.modules.pop("tennis_history", None)

print()
print("=" * 72)
print(f"OFFLINE RESULT: {len(PASS)} passed, {len(FAIL)} failed")
for n, d in FAIL:
    print(f"   - {n}   {d}")
print("=" * 72)

if "--live" not in sys.argv:
    print("\nThe deep source is UNVERIFIED until this is run with --live somewhere")
    print("that can reach raw.githubusercontent.com/JeffSackmann.")
    sys.exit(1 if FAIL else 0)

# ---------------------------------------------------------------- live ------
print()
print("=" * 72)
print("LIVE: fetching the real archives")
print("=" * 72)
report = []
rows = th.fetch(years=3, report=report)
okf = [r for r in report if r["ok"]]
print(f"  {len(okf)}/{len(report)} files parsed, {len(rows)} matches")
for r in report:
    status = "ok  " if r["ok"] else "SKIP"
    print(f"    {status} {r['file']:<34s} {r['kind']:<11s} rows={r['rows']:<7d} "
          f"{r['why'] or ''}")
if not rows:
    print("\n  NOTHING FETCHED -- the deep source is not usable here.")
    print("  tennis_elo will keep running on the Kalshi store alone (no regression).")
    sys.exit(1)

# show a real header so the assumed layout is on the record
for tour, repo, tmpl, kind in th._SOURCES:
    import clock
    txt = th._fetch(repo, tmpl.format(year=clock.today_et().year - 1))
    if txt:
        hdr = txt.split("\n", 1)[0]
        print(f"\n  real header of {tmpl}:")
        print(f"    {hdr[:300]}")
        print(f"    resolved -> {th._resolve(hdr.split(','))}")
        break

import collections
print(f"\n  surface coverage: {dict(collections.Counter(r[4] for r in rows))}")
print(f"  date range: {rows[0][0]} -> {rows[-1][0]}")
cnt = collections.Counter()
for _, _, w, l, _, _ in rows:
    cnt[w] += 1; cnt[l] += 1
tot = len(cnt) or 1
print(f"  players: {tot};  8+ matches: {sum(1 for v in cnt.values() if v>=8)} "
      f"({sum(1 for v in cnt.values() if v>=8)/tot*100:.0f}%);  "
      f"20+: {sum(1 for v in cnt.values() if v>=20)} "
      f"({sum(1 for v in cnt.values() if v>=20)/tot*100:.0f}%)")
print("\n  (compare the Kalshi-only pool: 29% at 8+, 1.5% at 20+)")
print("\n  NEXT: re-run tests/tennis_elo_fit.py -- K=48 was fitted on the SHALLOW")
print("  pool, and a deeper history usually wants a LOWER K. The fit test prints a")
print("  drift warning when the shipped value no longer matches.")
sys.exit(0)
