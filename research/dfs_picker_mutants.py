"""Mutation check for tests/guards_picker.py (research/reports/dfs_picker.md section 7): each mutant reintroduces one
bug -- one a review found, or one a guard exists to stop -- and the guard file must FAIL with the check named for it.
A run that fails under another check's name, or that never ran a check (killed by a signal: a worker restart once
read as three misses), is not counted as caught.

It EDITS the files it mutates and restores them afterwards. Run it on a copy of the tree, never on a tree a server
or another run is using:

    tar --exclude=./.git --exclude='__pycache__' -cf - . | (mkdir -p /tmp/pk && tar -xf - -C /tmp/pk)
    PK_ROOT=/tmp/pk python3 research/dfs_picker_mutants.py [--dry] [--shard=i/n] [--from=name] [names...]

Crash-safe: the original of the file in flight is kept in the temp directory and restored on the next start. Each
guard run takes 3-5 minutes; the 66 mutants take about an hour and a half on four copies (--shard=0/4 ... 3/4)."""
import json
import os
import subprocess
import sys
import tempfile
import zlib

ROOT = os.environ.get("PK_ROOT") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
INFLIGHT = os.path.join(tempfile.gettempdir(),
                        "pk_mutant_inflight_%08x.json" % zlib.crc32(os.path.realpath(ROOT).encode()))   # stable across runs
M = [
    ("no seat", "nfl_dfs.py", '    for p in sorted(fixed or (), key=lambda x: x["pos"] != "QB"):',
     '    for p in sorted((), key=lambda x: x["pos"] != "QB"):', "the sheet's picked fill, mechanism by mechanism"),
    ("no reserve", "nfl_dfs.py", "        if asc is not None:\n            rest = _reserve(",
     "        if False:\n            rest = _reserve(", "the sheet's picked fill, mechanism by mechanism"),
    ("no pre-pick", "nfl_dfs.py", "    if asc is not None and stack_team:\n        def choose(cands):",
     "    if False:\n        def choose(cands):", "the sheet's search with forced players"),
    ("no bring recount", "nfl_dfs.py",
     '            brought = sum(1 for q in lineup if q.get("team") == stack_opp and q["pos"] in ("WR", "TE"))\n', "",
     "the sheet's picked fill, mechanism by mechanism"),
    ("bias in values", "nfl_dfs.py", "    # a player the picker forced in rides a marked copy (never the field's dict)\n    return v + _PK_BIAS if \"_pk\" in p else v\n",
     "    # a player the picker forced in rides a marked copy (never the field's dict)\n    return v + _PK_BIAS\n", "the bias rides only a marked copy"),
    ("captain bias 1.5x", "nfl_dfs.py", '            cap_val -= (CPT_MULT - 1.0) * _PK_BIAS\n', '            pass\n',
     "the showdown sheet with picks equals a brute force"),
    ("one entry per captain", "nfl_dfs.py",
     "                          per_captain=(max(1, int(n)) if pick and pick.captain else 1))",
     "                          per_captain=1)", "the showdown sheet with picks equals a brute force"),
    ("field picked", "nfl_dfs.py", "                               field_players=(players if pk else None), ref=unpicked_proj)",
     "                               field_players=None, ref=unpicked_proj)", "meets the unpicked build's field"),
    ("exposure caps forced", "nfl_dfs.py", "                capped -= set(pk.force) if pk else set()\n", "",
     "a picked sheet build: every lineup holds"),
    ("no uniq clamp", "nfl_dfs.py", "        uniq = max(1, len(ROSTER) - len(pk.force))\n", "", "a picked sheet build: every lineup holds"),
    ("copies before adjust", "nfl_dfs.py",
     '        fixed = [dict(p, _pk=True) for p in players + restored if p["name"] in pk.force]',
     '        fixed = [dict(p, _pk=True, proj=round(p["proj"] / 1.1, 1)) for p in players + restored if p["name"] in pk.force]',
     "a picked sheet build: every lineup holds"),
    ("rank gate refused", "nfl_dfs.py", '        if "by depth chart" not in why:\n            return [], f"{nm} cannot be forced: {why}"',
     '        if True:\n            return [], f"{nm} cannot be forced: {why}"', "a man forced past the depth chart's RANK"),
    ("dst rule same on board", "nfl_dfs.py",
     '        for o in sorted(nm for nm in fo if pos_of(nm) != "DST" and (not board or pos_of(nm) in ("QB", "RB"))):',
     '        for o in sorted(nm for nm in fo if pos_of(nm) != "DST"):', "a pick the slate cannot hold is refused"),
    ("normalised first", "nfl_dfs.py", "    if not hits:\n        nk = norm(name)", "    if True:\n        hits = {}\n        nk = norm(name)",
     "identity is DraftKings' own spelling"),
    ("team cap scalar", "dfs_tourney.py",
     "    cap_t = np.maximum(int(max_per_team), np.bincount(team[fz], minlength=len(teams)))",
     "    cap_t = np.full(len(teams), max(int(max_per_team), int(np.bincount(team[fz], minlength=len(teams)).max())))",
     "the boards' rulebook with forced men"),
    ("punt role kept", "dfs_tourney.py", "~(punt & ~role[idx] & ~F).any(axis=1)", "~(punt & ~role[idx]).any(axis=1)",
     "the boards' rulebook with forced men"),
    # the filter alone is a backstop (graft never places a man in a row holding him: the has masks), so removing
    # it alone changed nothing (an equivalent mutant, MISSED); this one removes the protection and the backstop
    ("graft keeps dups", "dfs_tourney.py",
     (("            put = ~has & free\n", "            put = free\n"),
      ("    keep_rows = (srt[:, 1:] != srt[:, :-1]).all(axis=1) & (sal[out].sum(axis=1) <= cap)",
       "    keep_rows = sal[out].sum(axis=1) <= cap")),
     None, "the graft puts every forced man"),
    ("field draw masked", "dfs_tourney.py", "        idx_f = draw(int(field_n), rng, fb, fk, report=rep_f, pulls=pulls)",
     "        idx_f = draw(int(field_n), rng, fb, fk, report=rep_f, pulls=pulls, exclude=excl_m)",
     "a picked classic board at the unpicked board's seed keeps the FIELD"),
    ("no holds mask", "dfs_tourney.py",
     "        allowed &= (forced_m[idx_k].sum(axis=1) == int(forced_m.sum())) & ~excl_m[idx_k].any(axis=1)",
     "        pass", "a picked classic board at the unpicked board's seed keeps the FIELD"),
    ("extras added", "dfs_tourney.py",
     '            if "by depth chart" in why and nm in by_name:\n                # D3: adding a row',
     '            if False:\n                # D3: adding a row', "a man forced from outside the pool"),
    ("cpt any", "dfs_tourney.py", "            return p[\"name\"] == pk.captain          # his captain, the captain rules waived for him",
     "            return True", "a picked showdown board narrows only"),
    ("cap drops forced", "dfs_tourney.py",
     '        if drop["name"] in forced:\n            # D3, as the classic board',
     '        if False:\n            # D3, as the classic board', "a picked showdown board narrows only"),
    ("pending by plain", "app.py", 'if (_tourney_built_ts(r.get("sport") or "nfl", dg, pick=bool(r.get("pick"))) or 0) >= (r.get("ts") or 0):',
     'if (_tourney_built_ts(r.get("sport") or "nfl", dg) or 0) >= (r.get("ts") or 0):', "a picked Build is checked"),
    ("no request check", "app.py", "            if why:\n                return jsonify({\"error\": f\"player picker: {why}\", \"pick_refused\": True}), 400",
     "            if False:\n                return jsonify({\"error\": f\"player picker: {why}\", \"pick_refused\": True}), 400",
     "a picked Build is checked"),
    ("route reads exclude", "app.py", '            pick = nfl_dfs.pick_parse(d.get("pick"))', '            pick = nfl_dfs.pick_parse({"exclude": d.get("exclude") or []})',
     "the sheet's Run carries the pick"),
    ("schedule sees picks", "pc_worker.py", '            {dg: r for dg, r in wanted.items() if not r.get("pick")})',
     "            dict(wanted))", "one slate owing a picked Build"),
    ("picked consumes window", "pc_worker.py", '        art["requested_by"] = {"by": "PC request (picked)", "why": why}\n        boardshare.put(name, art)',
     '        art["requested_by"] = {"by": "PC request (picked)", "why": why}\n        boardshare.put(name, art)\n        _consume_window(_tourney_state(), dg, "x", kind="cl_windows")',
     "the schedule's own build calls carry no pick"),
    ("dfslog no tag", "dfslog.py", '        objective = "picked:" + objective', "        pass", "the look-back takes a picked build"),
    ("hindsight reads picked", "dfs_hindsight.py", '_BOARD = re.compile(r"^(cl|sd)_tourney_nfl_(\\d+)\\.pkl$")',
     '_BOARD = re.compile(r"^(cl|sd)_tourney_nfl_(\\d+)(_pick)?\\.pkl$")', "the look-back takes a picked build"),
    ("js raw name", "static/dfspick.js", "<span class=\"pk-who\"><b>${_pkEsc(r.name)}</b>", "<span class=\"pk-who\"><b>${r.name}</b>",
     "the picker's own logic under node"),
    ("js raw banner", "static/dfspick.js", "  const waived = (pk.waived || []).map((w) => `<li>${_pkEsc(w)}</li>`).join(\"\");",
     "  const waived = (pk.waived || []).map((w) => `<li>${w}</li>`).join(\"\");", "the picker's own logic under node"),
    ("js captain not forced", "static/dfspick.js", "      if (!s.force.some(same)) s.force.push(_pkEntry(r));\n    }\n  }",
     "    }\n  }", "the picker's own logic under node"),
    ("js bare exclude", "static/app.js", '        pick: (sp === "nfl" && typeof dfsPickPayload === "function") ? dfsPickPayload("sheet") : null,',
     '        exclude: (sp === "nfl" && typeof dfsPickPayload === "function") ? dfsPickPayload("sheet") : null,',
     "the picker is wired"),
    # Fable's build review, M4: the solver, the board's entry rules, the PC's picked path
    ("second solve unbiased", "dfs_tourney.py", "        X2[forced_m[keep2]] += _SAFE_FORCE\n",
     "        X2[forced_m[keep2]] += 0.0\n", "own candidates come from the SECOND solve"),
    ("steady ignores pick", "dfs_tourney.py",
     "        safe2 = safe_lineups(X, ents, keep2, gen, n_cols=2 * _SAFE_COLS, forced=forced_m)",
     "        safe2 = safe_lineups(X, ents, keep2, gen, n_cols=2 * _SAFE_COLS, forced=None)",
     "own candidates come from the SECOND solve"),
    ("steady pairs unfit", "dfs_tourney.py",
     "                        and kp[j].get(\"team\") == kp[q].get(\"team\") and not kp[j].get(\"_field_only\")\n"
     "                        and fits(q, j)),",
     "                        and kp[j].get(\"team\") == kp[q].get(\"team\") and not kp[j].get(\"_field_only\")),",
     "the boards' rulebook with forced men binds the free slots"),
    ("graft catcher cheapest", "dfs_tourney.py",
     "            v[np.arange(len(r)), sl[np.argmax(cost[r], axis=1)]] = c",
     "            v[np.arange(len(r)), sl[np.argmin(np.where(cost[r] < 0, 10 ** 9, cost[r]), axis=1)]] = c",
     "the graft puts every forced man"),
    ("entry_ok tests forced", "dfs_tourney.py",
     "            if p[\"name\"] in forced:\n                continue\n"
     "            if p.get(\"_field_only\") or not nfl_dfs._sd_allowed(p, cap_p, got):",
     "            if p.get(\"_field_only\") or not nfl_dfs._sd_allowed(p, cap_p, got):",
     "a picked showdown board narrows only"),
    ("sd empty published", "dfs_tourney.py", "    if pk and not allowed.any():\n        # a board of empty lists",
     "    if False:\n        # a board of empty lists", "a picked showdown board narrows only"),
    ("cl empty published", "dfs_tourney.py", "        if not allowed.any():\n            # a board of empty lists",
     "        if False:\n            # a board of empty lists", "a man forced from outside the pool"),
    ("ref unpinned", "nfl_dfs.py", "                               field_players=(players if pk else None), ref=unpicked_proj)",
     "                               field_players=(players if pk else None), ref=None)", "meets the unpicked build's field"),
    ("no salary bound", "nfl_dfs.py", "    if cost > cap:\n        return None, (f\"your picks cost ${cost:,}",
     "    if False:\n        return None, (f\"your picks cost ${cost:,}", "a pick the slate cannot hold is refused"),
    ("route serves plain", "app.py", "    payload, age = _tourney_board(sport, dg, pick=picked)",
     "    payload, age = _tourney_board(sport, dg)", "the board route serves the picked board"),
    ("picked sims as plain", "pc_worker.py", "n_sims=_cl_sims(st, plain, picked=True)", "n_sims=_cl_sims(st, plain)",
     "a picked showdown request keeps the plain path's window"),
    ("cl_sims ignores picked", "pc_worker.py", "                                      if picked else 0.0)",
     "                                      if False else 0.0)", "a picked showdown request keeps the plain path's window"),
    ("picked sd no window", "pc_worker.py", "        if kind == \"showdown\":\n            # the plain path's own showdown rules",
     "        if False:\n            # the plain path's own showdown rules", "a picked showdown request keeps the plain path's window"),
    # the Opus round-2 review's items
    ("cl off-chart extra kept", "dfs_tourney.py",
     "                    if \"by depth chart\" not in why:\n                        # the sheet's rule (nfl_dfs._pk_restore): only the depth chart's RANK is waived; a man",
     "                    if False:\n                        # the sheet's rule (nfl_dfs._pk_restore): only the depth chart's RANK is waived; a man",
     "a man forced from outside the pool"),
    ("sd roster extra kept", "dfs_tourney.py",
     "                    if \"by depth chart\" not in why:\n                        # the sheet's rule (nfl_dfs._pk_restore): only the depth chart's RANK is waived; the",
     "                    if False:\n                        # the sheet's rule (nfl_dfs._pk_restore): only the depth chart's RANK is waived; the",
     "a picked showdown board narrows only"),
    ("steady bonus 1x", "dfs_tourney.py", "    V[fk, :] += 4.0 * _SAFE_FORCE\n", "    V[fk, :] += _SAFE_FORCE\n",
     "own candidates come from the SECOND solve"),
    ("graft brings excluded", "dfs_tourney.py",
     " and not (excluded is not None and excluded[j])),", "),", "the graft puts every forced man"),
    ("graft call unexcluded", "dfs_tourney.py", "        idx_c = graft(idx_c, ents, forced_m, excluded=excl_m)\n",
     "        idx_c = graft(idx_c, ents, forced_m)\n", "the graft puts every forced man"),
    ("free captain unnamed", "dfs_tourney.py",
     "            if not nfl_dfs._sd_allowed(q, cap, got):\n                beside.setdefault(q[\"name\"], set()).add(cap[\"name\"])",
     "            if False:\n                beside.setdefault(q[\"name\"], set()).add(cap[\"name\"])",
     "a forced FLEX breaking a construction rule beside a FREE"),
    ("refusal every cycle", "pc_worker.py",
     "            if time.time() - (last.get(\"at\") or 0) < wait:\n                continue",
     "            if False:\n                continue", "a picked Build the picker refused is tried again only"),
    ("pk_what holding only", "nfl_dfs.py",
     "        + ([f\"without {', '.join(sorted(pk.exclude))}\"] if pk.exclude else [])",
     "        + []", "a pick the slate cannot hold is refused"),
    ("csv input ignored", "static/dfspick.js", "  if (t.id === \"dfsCsv\") { dfsPickLoadSheet(); return; }\n", "",
     "the picker follows the page"),
    ("pc payload any slate", "static/dfspick.js",
     "  if (ctx === \"pc\" && (!s.dg || s.key !== `dg:${s.dg}` || (dg != null && String(dg) !== s.dg))) return null;",
     "  if (ctx === \"pc\" && !s.dg) return null;", "the picker follows the page"),
    ("old captain kept", "static/dfspick.js",
     "      if (old) s.force = s.force.filter((p) => !(p.name === old.name && p.team === old.team));\n", "",
     "the picker follows the page"),
    ("file fill not redrawn", "static/app.js",
     "      // a programmatic fill fires no input event: the player picker reads the new pool here\n"
     "      if (typeof dfsPickLoadSheet === \"function\") dfsPickLoadSheet();\n", "", "the picker follows the page"),
    # the Opus round-3 review's leftovers
    ("throttle any refusal", "pc_worker.py", "            if why_r.startswith(\"player picker:\"):",
     "            if True:", "a picked Build the picker refused is tried again only"),
    ("no lock cap", "pc_worker.py",
     "            wait = _PICK_RETRY_S if st is None else min(_PICK_RETRY_S, max(600.0, (st - time.time()) / 2.0))",
     "            wait = _PICK_RETRY_S", "a picked Build the picker refused is tried again only"),
    ("refusals kept", "pc_worker.py", "    for k in gone:\n        del refused[k]", "    for k in gone:\n        pass",
     "a picked Build the picker refused is tried again only"),
    ("one captain named", "nfl_dfs.py", "    caps = sorted(captains)\n", "    caps = sorted(captains)[:1]\n",
     "a forced FLEX breaking a construction rule beside a FREE"),
    ("build ignores loading", "static/app.js",
     "    if (typeof dfsPickReady === \"function\") await dfsPickReady(dg);\n", "", "the picker follows the page"),
    ("ready ignores pending", "static/dfspick.js",
     "  return s.pending && s.pending.dg === String(dg) ? s.pending.p : Promise.resolve();",
     "  return Promise.resolve();", "the picker follows the page"),
    # the Opus round-5 review's nit: the pin covers every clause of `fits`
    ("fits WR 5", "dfs_tourney.py", 'g("WR", 0) <= 4', 'g("WR", 0) <= 5',
     "the boards' rulebook with forced men binds the free slots"),
    ("fits no RB+WR", "dfs_tourney.py", '\n                and g("RB", 0) + g("WR", 0) <= 6)', ')',
     "the boards' rulebook with forced men binds the free slots"),
]


def _recover():
    if os.path.exists(INFLIGHT):
        d = json.load(open(INFLIGHT))
        open(d["path"], "w").write(d["src"])
        os.remove(INFLIGHT)
        print("RECOVERED", d["path"], "from an interrupted run")


def main():
    _recover()
    args = sys.argv[1:]
    dry = "--dry" in args
    args = [a for a in args if a != "--dry"]
    start, shard = None, None
    if args and args[0].startswith("--from="):
        start, args = args[0][len("--from="):], args[1:]
    if args and args[0].startswith("--shard="):
        i, n = args[0][len("--shard="):].split("/")
        shard, args = (int(i), int(n)), args[1:]
    only = set(args)
    todo = [m for m in M if not only or m[0] in only]
    if shard:
        todo = [m for k, m in enumerate(todo) if k % shard[1] == shard[0]]
    if start:
        todo = todo[[m[0] for m in todo].index(start):]
    bad = []
    for name, f, old, new, expect in todo:
        path = os.path.join(ROOT, f)
        src = open(path).read()
        pairs = old if isinstance(old, tuple) else ((old, new),)
        counts = [src.count(o) for o, _n in pairs]
        if counts != [1] * len(pairs):
            bad.append((name, f"anchor count {counts}"))
            print("SKIP", name, "anchor count", counts)
            continue
        mutated = src
        for o, n in pairs:
            mutated = mutated.replace(o, n)
        if dry:
            print("ANCHOR OK", name)
            continue
        try:
            json.dump({"path": path, "src": src}, open(INFLIGHT, "w"))
            open(path, "w").write(mutated)
            for d in (os.path.join(ROOT, "__pycache__"), os.path.join(ROOT, "tests", "__pycache__")):
                subprocess.run(["rm", "-rf", d])
            r = subprocess.run([sys.executable, "tests/guards_picker.py"], cwd=ROOT, capture_output=True,
                               text=True, timeout=1500)
        finally:
            open(path, "w").write(src)
            if os.path.exists(INFLIGHT):
                os.remove(INFLIGHT)
        fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL")]
        npass = sum(1 for ln in r.stdout.splitlines() if ln.startswith("PASS"))
        if r.returncode < 0 or not (npass or fails):
            # killed (a signal, a restart) or never ran a check: no verdict either way -- the run the worker restart
            # interrupted printed "MISSED ... 0 fails" for three mutants, with nothing to tell a kill from a miss
            print(f"ERROR {name} | rc {r.returncode}, {npass} passed, {len(fails)} failed: no verdict; re-run it",
                  r.stderr[-300:], flush=True)
            bad.append((name, "error"))
            continue
        caught = r.returncode != 0 and any(expect in ln for ln in fails)
        print(("CAUGHT " if caught else "MISSED ") + name, "|", f"rc {r.returncode},", npass, "passed,", len(fails), "fails",
              "" if caught else (" || ".join(ln[:160] for ln in fails) + " " + r.stderr[-400:]), flush=True)
        if not caught:
            bad.append((name, "missed"))
    print(f"{len(todo) - len(bad)} anchors match, {len(bad)} do not" if dry
          else f"{len(todo) - len(bad)} caught, {len(bad)} missed or without a verdict")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
