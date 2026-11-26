"""High-performance build profiles for the PC (2026-11-26). vigil-pc.cfg gains

    "profile": "standard" | "deep" | "maximum" | "research" | "custom"

and a classic tournament build reads its world counts, field size, processes
and disk limits from it. Pure arithmetic, no imports of the builders: the
guards drive every rule with plain dicts.

**standard is production, number for number** (pc_worker._CL_SIMS_FULL =
20,000 generation + 160,000 ranking worlds, dfs_evaluate.FINAL_N 40,000 final
worlds, a 300,000-lineup field, every core, pool_cache's 24 entries / 12 GB).
With no "profile" key -- every PC today -- nothing changes: pc_worker makes
the calls it always made, and `choose` is not consulted for a count.

The three world sets stay independent, as they are now (dfs_tourney
.world_split; dfs_evaluate.final_seed): the first `generation` worlds of a
board's pool find the candidates, the next `ranking` worlds score them, and
the `final` worlds are a separate pool on its own seed that the lineup
evaluator alone reads.

The larger profiles are RESEARCH TARGETS, not a recommendation: cl_worlds.md
measured 160,000 ranking worlds 1.47 SE better than 40,000 on the money list
and found nothing from a 1.2M field, and no convergence experiment has been
run past that (they wait on the GPU tournament path). They exist so one can
be run and its board compared: every board is stamped with the profile it
ran, the one asked for and why they differ (`choose`'s `why`).

A profile is used only when it can land:
  * time -- its run time is scaled from the last timed board's own stages
    (`estimate_s`: the sims by worlds, the best lineups by generation worlds,
    the field by its size, the scoring pass by ranking worlds x (candidates +
    field), the portfolio pass by ranking worlds x field) and must fit before
    lock with TIME_MARGIN x and TIME_PAD_S to spare, else the next profile
    down is tried, and standard (pc_worker._cl_sims' own full/quick rule) is
    the floor;
  * memory -- `memory_gb` must fit MEM_FRACTION of the PC's RAM. The binding
    term is the pool itself: nfl_dfs_sim hands every player's worlds over as
    a Python list (~32 bytes a score against 4 in the float32 matrix), so on
    a 500-player slate maximum needs ~58 GB and research ~120 GB. Until the
    pool travels as a matrix those two fall back on a 32 GB PC, and say so;
  * the window -- the 85-40-minute pre-lock rebuild is always standard (it
    exists to catch the inactives; pc_worker._CL_PREGAME_MIN).
"""

ORDER = ("standard", "deep", "maximum", "research")
# generation / ranking / final worlds, the classic field sample, the worlds
# cache's disk limit. standard's row is today's production; the others are the
# owner's research targets (2026-11-26: "deep: 50k / 500k / 160k; maximum:
# 100k / 1M / 500k, with a 1M field; research: 250k / 2M-5M / 1M-2M, with a 3M
# field" -- research takes the low end of each range, "custom" reaches the rest).
PROFILES = {
    "standard": {"generation": 20_000, "ranking": 160_000, "final": 40_000, "field": 300_000,
                 "gpu_vram_fraction": None, "cache_gb": 12.0, "cache_entries": 24},
    "deep": {"generation": 50_000, "ranking": 500_000, "final": 160_000, "field": 300_000,
             "gpu_vram_fraction": 0.6, "cache_gb": 24.0, "cache_entries": 24},
    "maximum": {"generation": 100_000, "ranking": 1_000_000, "final": 500_000, "field": 1_000_000,
                "gpu_vram_fraction": 0.8, "cache_gb": 48.0, "cache_entries": 24},
    "research": {"generation": 250_000, "ranking": 2_000_000, "final": 1_000_000, "field": 3_000_000,
                 "gpu_vram_fraction": 0.9, "cache_gb": 96.0, "cache_entries": 24},
}
_COUNTS = ("generation", "ranking", "final", "field")
_LIMITS = {"generation": (1_000, 1_000_000), "ranking": (10_000, 10_000_000), "final": (1_000, 5_000_000),
           "field": (10_000, 5_000_000)}
CHECKPOINT_GB = 8.0             # build_ckpt.MAX_BYTES, in GB
TIME_MARGIN = 1.25              # the estimate is an extrapolation from one board: a quarter over
TIME_PAD_S = 900.0              # and fifteen minutes (pc_worker._cl_sims keeps ten on a measured build)
MEM_FRACTION = 0.8              # of the PC's physical memory
GUESS_S = 2700.0                # a standard build with no timed board (pc_worker._CL_FULL_GUESS_S)
_POOL_BYTES = 44.0              # a score in memory: a Python float in a list (32) + X, Xe and their shared copy (3 x 4)
_LINEUP_BYTES = 12.0            # a lineup x player cell: the float32 matrix, its transpose, the shared copy
_DEF_PLAYERS, _DEF_CONTESTS, _DEF_CANDIDATES, _DEF_PORT = 500, 3, 150_000, 1200
PREGAME = "pregame"             # pc_worker._cl_window_key's suffix for the post-inactives window


def _num(v, lo, hi):
    """v as an int inside [lo, hi], or None (a bool is not a number here)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = int(v)
    return v if lo <= v <= hi else None


def _frac(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not (0.05 <= float(v) <= 0.95):
        return None
    return float(v)


def _gb(v, lo=1.0, hi=4000.0):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not (lo <= float(v) <= hi):
        return None
    return float(v)


def from_cfg(cfg):
    """(profile, notes) for vigil-pc.cfg's contents (a dict; anything else is
    standard). The profile is {name, generation, ranking, final, field,
    workers, gpu_vram_fraction, cache_gb, cache_entries, checkpoint_gb};
    `notes` says what was ignored and why -- a typo must never stop the
    worker, and never silently run something the owner did not ask for.

    "custom" reads "profile_custom": {generation_worlds, ranking_worlds,
    final_worlds, field, workers, gpu_vram_fraction, cache_gb}; a number
    missing or out of range is standard's. The top-level "workers",
    "gpu_vram_fraction", "cache_gb", "cache_entries" and "checkpoint_gb"
    override any profile's (workers None = every core, as now)."""
    cfg = cfg if isinstance(cfg, dict) else {}
    notes = []
    name = cfg.get("profile")
    if name is None:
        name = "standard"
    if not isinstance(name, str) or name.lower() not in ORDER + ("custom",):
        notes.append(f"unknown profile {name!r}: standard used")
        name = "standard"
    name = name.lower()
    p = dict(PROFILES["standard" if name == "custom" else name], name=name, workers=None,
             checkpoint_gb=CHECKPOINT_GB)
    if name == "custom":
        c = cfg.get("profile_custom")
        c = c if isinstance(c, dict) else {}
        for key, src in (("generation", "generation_worlds"), ("ranking", "ranking_worlds"),
                         ("final", "final_worlds"), ("field", "field")):
            if src in c:
                v = _num(c[src], *_LIMITS[key])
                if v is None:
                    notes.append(f"profile_custom.{src} {c[src]!r} is not a count in {_LIMITS[key]}: standard's used")
                else:
                    p[key] = v
        # the generation worlds are at most a third of a build's (dfs_tourney.world_split would cut them silently)
        if p["generation"] * 2 > p["ranking"]:
            notes.append("profile_custom: generation worlds held to half the ranking worlds")
            p["generation"] = p["ranking"] // 2
        cfg = dict(cfg, **{k: c[k] for k in ("workers", "gpu_vram_fraction", "cache_gb") if k in c and k not in cfg})
    if "workers" in cfg and cfg["workers"] is not None:
        v = _num(cfg["workers"], 1, 256)
        if v is None:
            notes.append(f"workers {cfg['workers']!r} is not a process count: every core used")
        p["workers"] = v
    if "gpu_vram_fraction" in cfg and cfg["gpu_vram_fraction"] is not None:
        v = _frac(cfg["gpu_vram_fraction"])
        if v is None:
            notes.append(f"gpu_vram_fraction {cfg['gpu_vram_fraction']!r} is not between 0.05 and 0.95: ignored")
        else:
            p["gpu_vram_fraction"] = v
    for key in ("cache_gb", "checkpoint_gb"):
        if key in cfg and cfg[key] is not None:
            v = _gb(cfg[key])
            if v is None:
                notes.append(f"{key} {cfg[key]!r} is not a size in GB: {p[key]:g} used")
            else:
                p[key] = v
    if "cache_entries" in cfg and cfg["cache_entries"] is not None:
        v = _num(cfg["cache_entries"], 1, 1000)
        if v is None:
            notes.append(f"cache_entries {cfg['cache_entries']!r} is not a count: {p['cache_entries']} used")
        else:
            p["cache_entries"] = v
    return p, notes


def is_standard_counts(p):
    return all(p[k] == PROFILES["standard"][k] for k in _COUNTS)


def n_sims(p):
    """The worlds one board simulates: generation + ranking (the final worlds
    are the evaluator's own pool)."""
    return int(p["generation"]) + int(p["ranking"])


def build_kwargs(p):
    """dfs_tourney.build_nfl_classic's arguments for a profile."""
    kw = {"n_sims": n_sims(p), "opt_worlds": int(p["generation"]), "field_n": int(p["field"])}
    if p.get("workers"):
        kw["workers"] = int(p["workers"])
    return kw


def _board_shape(board):
    """(players, contests, candidates) the last board had, else defaults."""
    b = board or {}
    return (len(b.get("players") or ()) or _DEF_PLAYERS, len(b.get("contests") or ()) or _DEF_CONTESTS,
            int(b.get("candidates") or 0) or _DEF_CANDIDATES)


def estimate_s(p, board, picked=False):
    """Seconds a classic build at profile `p` takes on this PC, scaled from
    the last board's own stage times: (seconds, how it was reached). A board
    with no timings (or none at all) scales GUESS_S, a standard build, by the
    larger of the worlds ratio and the scoring-work ratio -- deliberately the
    pessimistic one."""
    std = PROFILES["standard"]
    t = (board or {}).get("timings") or {}
    b = board or {}
    sims0, gen0, ev0 = b.get("sims"), b.get("opt_worlds"), b.get("eval_worlds")
    f0 = ((b.get("field_model") or {}).get("n")) or 0
    k0 = b.get("candidates") or 0
    if not (t.get("total_s") and sims0 and gen0 and ev0 and f0) or t.get("resumed"):
        # (a resumed build's stage times are the restart's, not a build's: pc_worker marks them)
        work = (p["ranking"] * (p["field"] + _DEF_CANDIDATES)) / float(std["ranking"] * (std["field"] + _DEF_CANDIDATES))
        est = GUESS_S * max(1.0, n_sims(p) / float(n_sims(std)), work)
        return est * (1.15 if picked else 1.0), "no timed board on this PC: a 45-minute standard build, scaled"
    sims, opt = float(t.get("sims_s") or 0.0), float(t.get("optimal_s") or 0.0)
    fld, score, port = float(t.get("field_s") or 0.0), float(t.get("score_s") or 0.0), float(t.get("portfolio_s") or 0.0)
    fixed = max(0.0, float(t["total_s"]) - sims - opt - fld - score - port)
    fr = max(1.0, p["field"] / float(f0))           # a smaller field is not credited: steering is a fixed cost
    est = (fixed + sims * n_sims(p) / float(sims0) + opt * p["generation"] / float(gen0) * (2.0 if picked else 1.0)
           + fld * fr
           + score * (p["ranking"] / float(ev0)) * ((p["field"] + k0) / float(f0 + k0))
           + port * (p["ranking"] / float(ev0)) * (p["field"] / float(f0)))
    return est, f"scaled from the last board's stages ({int(sims0):,} worlds in {float(t['total_s']):.0f} s)"


def memory_gb(p, players=_DEF_PLAYERS, contests=_DEF_CONTESTS, candidates=_DEF_CANDIDATES, port=_DEF_PORT):
    """Peak memory of a classic build at profile `p`, in GB: the pool as
    Python lists and its float32 copies, the lineup matrices (three copies),
    the portfolio pass's make-it matrix and its concatenation, and 2 GB for
    the scoring chunks (dfs_tourney._CHUNK_ELEMS, divided over the workers)
    and everything else."""
    b = (players * n_sims(p) * _POOL_BYTES
         + players * (p["field"] + candidates) * _LINEUP_BYTES
         + 2.0 * 4.0 * contests * port * p["ranking"])
    return b / 1024 ** 3 + 2.0


def choose(requested, start_ts, now, board, window=None, picked=False, ram_gb=None):
    """The profile a classic build runs: {"name", ...the profile's numbers,
    "requested", "why", "estimate_s", "memory_gb"}. `requested` is from_cfg's
    profile; the answer is it, or the largest one below it that fits the time
    to lock and this PC's memory, or standard. `window` is pc_worker
    ._cl_window_key's key. "standard" means: make the calls production makes
    (pc_worker._cl_sims still chooses 180,000 or 60,000 by its own rule)."""
    std = dict(PROFILES["standard"], name="standard", workers=requested.get("workers"),
               checkpoint_gb=requested.get("checkpoint_gb", CHECKPOINT_GB))

    def out(p, why, est=None, mem=None):
        return dict(p, requested=requested["name"], why=why,
                    estimate_s=(round(float(est), 0) if est is not None else None),
                    memory_gb=(round(float(mem), 1) if mem is not None else None))
    if requested["name"] == "standard" or (requested["name"] == "custom" and is_standard_counts(requested)):
        return out(std, "the configured profile")
    if window and str(window).endswith(PREGAME):
        return out(std, "the pre-lock rebuild always runs standard: it exists to catch the inactives")
    if requested["name"] == "custom":
        ladder = [requested]
    else:
        ladder = [dict(PROFILES[n], name=n, workers=requested.get("workers"),
                       checkpoint_gb=requested.get("checkpoint_gb", CHECKPOINT_GB))
                  for n in reversed(ORDER[1:ORDER.index(requested["name"]) + 1])]
        # the owner's own disk limits ride along on every rung
        for p in ladder:
            if requested["name"] == p["name"]:
                p.update({k: requested[k] for k in ("cache_gb", "cache_entries", "gpu_vram_fraction")})
    players, contests, cands = _board_shape(board)
    why_not = []
    for p in ladder:
        mem = memory_gb(p, players, contests, cands)
        if ram_gb is None:
            why_not.append(f"{p['name']}: this PC's memory could not be read (set \"ram_gb\" in vigil-pc.cfg)")
            continue
        if mem > MEM_FRACTION * float(ram_gb):
            why_not.append(f"{p['name']} needs ~{mem:.0f} GB of memory and this PC has {float(ram_gb):.0f}")
            continue
        est, how = estimate_s(p, board, picked)
        if start_ts is not None:
            left = float(start_ts) - float(now)
            if left < TIME_MARGIN * est + TIME_PAD_S:
                why_not.append(f"{p['name']} would take ~{est / 60.0:.0f} min ({how}) with {left / 60.0:.0f} min to lock")
                continue
        return out(p, "; ".join(why_not + [f"{p['name']} fits: ~{est / 60.0:.0f} min ({how}), ~{mem:.0f} GB"]), est, mem)
    return out(std, "; ".join(why_not) + "; standard used")


def final_worlds(requested, start_ts, now, board, ram_gb=None):
    """(final worlds for the lineup evaluator, why). The profile's count when
    the slate's lock is known and far enough for the first evaluation to
    simulate them -- the board's own simulation rate, tripled for the field's
    thresholds, with the build's margin -- and the memory holds them; else
    standard's 40,000 (dfs_evaluate.FINAL_N). A slate's final worlds are
    simulated once and kept, so the first evaluation decides."""
    std = PROFILES["standard"]["final"]
    n = int(requested.get("final") or std)
    if n == std:
        return std, "the configured profile"
    players, _c, _k = _board_shape(board)
    mem = players * n * _POOL_BYTES / 1024 ** 3 + 2.0
    if ram_gb is None or mem > MEM_FRACTION * float(ram_gb):
        return std, (f"{n:,} final worlds need ~{mem:.0f} GB of memory"
                     + (f" and this PC has {float(ram_gb):.0f}" if ram_gb is not None else
                        " and this PC's memory could not be read") + ": 40,000 used")
    t = (board or {}).get("timings") or {}
    sims0 = (board or {}).get("sims")
    if start_ts is None or not (t.get("sims_s") and sims0) or t.get("resumed"):
        return std, "no kickoff time or no timed board to scale from: 40,000 used"
    est = 3.0 * float(t["sims_s"]) * n / float(sims0)
    left = float(start_ts) - float(now)
    if left < TIME_MARGIN * est + TIME_PAD_S:
        return std, f"{n:,} final worlds would take ~{est / 60.0:.0f} min with {left / 60.0:.0f} min to lock: 40,000 used"
    return n, f"{requested['name']} profile: ~{est / 60.0:.0f} min for the slate's first evaluation"


def stamp(chosen, sims=None):
    """What a board records about the profile it ran (plain values)."""
    keep = ("name", "requested", "why", "generation", "ranking", "final", "field", "workers",
            "gpu_vram_fraction", "cache_gb", "estimate_s", "memory_gb")
    s = {k: chosen.get(k) for k in keep}
    if sims is not None and chosen.get("name") == "standard":
        # standard's two counts (pc_worker._cl_sims): the board says which ran
        s["ranking"] = int(sims) - int(s["generation"])
    return s
