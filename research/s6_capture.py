"""S6.0 -- pin the DraftKings half of the inputs, so S6 research runs offline.

After S5 the reproducibility position was: Sleeper projections pinned, DraftKings
slate and contest data still live-fetched. DAL @ NYG proved that is not a
theoretical gap -- the same draft group returned 427,048 legal lineups when the
live A/B ran and 583,082 four days later, because its DK player pool gained a
player. A stage about contest identity cannot be audited against inputs that move
underneath it.

So this captures, per draft group:

  slate      the full dk.slate_for payload (player pool, salaries, status, the
             CSV the builder parses) as returned, normalised only by JSON
             round-trip
  contests   the full dk.contests lobby listing for the group (which already
             carries fee, field size and max_entries_per_user), plus full
             dk.contest_detail -- the payout ladder, places paid, start time --
             for a DIVERSE SUBSET chosen by `select()`: the richest contests
             plus one of every entry-limit shape plus both field-size extremes.
             881 contests on one draft group is too many detail calls, and
             taking only the richest would have sampled only the 150-entry-max
             end while 751 of the 881 are single entry.

Each file gets sha256, byte size, record count, retrieval timestamp, the source
identifier it came from, and the normalisation version. Sleeper and DraftKings
hashes are kept in SEPARATE blocks in every artifact, because "pinned" for one
source is not pinned for the other and conflating them is how the previous
overclaim happened.

NO credentials are captured: dk's endpoints are the public lobby and contest
routes, and the snapshot is the response body only. `_scan` walks EVERY key of
every snapshot before it is written -- no truncation, and it raises rather than
returning a partial result. It first walked only the first 200 list elements,
which left 77% of an 881-row lobby listing uninspected while this paragraph
claimed otherwise.

`replay()` is the offline reader. Research modules take a capture directory and
never touch the network.
"""
import hashlib
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")
CAP = os.path.join(DATA, "dk_capture")

#: bump when the stored shape changes, so a hash from an older normalisation is
#: not silently compared against a newer one
NORM_VERSION = 1

DGS = (153086, 153085)

#: Substrings that must not appear in any snapshot KEY. The DK routes used here
#: are public and send no auth, but "it does not today" is not a guarantee.
#:
#: These are STEMS rather than exact field names, for two reasons. They cover
#: strictly more: each stem matches every casing and separator variant of its
#: family, so one short entry does the work of the half-dozen spellings a
#: hand-written list would have to enumerate. And enumerating those spellings
#: here would make this very line trip the repo's own pre-commit scan, whose
#: pattern is itself a list of credential-shaped literals -- which is exactly
#: why scripts/secret-scan.sh keeps its patterns inside the one file it skips.
#: A check that fires on its own documentation is a check people learn to
#: ignore. Matching is case-insensitive substring, so a stem also catches the
#: hyphenated and camelCase forms.
FORBIDDEN = ("cookie", "authorization", "session", "token", "secret", "bearer",
             "pass", "key", "credential", "auth")


def _sha(path):
    with open(path, "rb") as fh:
        b = fh.read()
    return hashlib.sha256(b).hexdigest(), len(b)


#: node budget for _scan. Generous enough for any DK payload seen (the largest
#: is ~880 lobby rows) and small enough to stop a pathological blob. Exhausting
#: it RAISES rather than returning a short answer -- see below.
SCAN_BUDGET = 2_000_000


def _scan(obj, path="$", budget=None):
    """EVERY key in a snapshot, so a credential cannot ride along unnoticed.

    Every key means every key. The first version of this walked only the first
    200 elements of any list, which on a lobby listing of 881 contests left 681
    rows -- 77% of the payload -- uninspected, while the module docstring claimed
    the files were guarded. A credential planted at index 400 was not detected;
    that was measured, not imagined. A partial scan that reports "clean" is worse
    than no scan, because it is quoted as evidence.

    So there is no truncation. There is a node BUDGET instead, and running out of
    it raises: a scan that cannot finish must fail the write, never return a
    short list that reads as a pass. Fail closed, loudly.
    """
    counter = [SCAN_BUDGET if budget is None else budget]

    def walk(o, p):
        counter[0] -= 1
        if counter[0] < 0:
            raise RuntimeError(
                f"credential scan exceeded its {SCAN_BUDGET:,}-node budget at {p}; "
                "refusing to report a partial scan as clean")
        out = []
        if isinstance(o, dict):
            for k, v in o.items():
                if any(f in str(k).lower() for f in FORBIDDEN):
                    out.append(f"{p}.{k}")
                out += walk(v, f"{p}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                out += walk(v, f"{p}[{i}]")
        return out

    return walk(obj, path)


#: how many contests to pull full detail for. This slate lists 881 on one draft
#: group, and a detail call each is both slow and rude to DK, so the capture
#: selects a DIVERSE subset instead of everything.
DETAIL_TOP = 12

#: the entry-limit buckets S6 has to be able to reason about. 751 of the 881
#: contests on draft group 153086 are SINGLE ENTRY, so a capture that only took
#: the richest contests would have sampled only the 150-max end and missed the
#: case that actually breaks a 20-entry portfolio.
MAX_USER_BUCKETS = ((1, 1), (2, 5), (6, 50), (51, 10 ** 9))


def select(rows, top=DETAIL_TOP, buckets=MAX_USER_BUCKETS):
    """Which contests to pull full detail for: the richest, plus guaranteed
    coverage of every entry-limit bucket, plus the smallest and largest fields.

    Deterministic GIVEN THE LOBBY LISTING -- sorted by prize pool then id, with
    no dependence on dict or network ordering. That is a weaker promise than it
    may read as: DraftKings adds and fills contests, so a later capture can see a
    different listing and legitimately select a different set. The determinism is
    over the input, not over time. The lobby row already carries entry_fee,
    entries and max_entries_per_user, so this selection needs no detail calls of
    its own.
    """
    def key(c):
        return (-(c.get("prize_pool") or 0), int(c.get("id") or 0))

    ordered = sorted((c for c in rows if c.get("id")), key=key)
    picked, seen = [], set()

    def add(c):
        cid = int(c["id"])
        if cid not in seen:
            seen.add(cid)
            picked.append(cid)

    for c in ordered[:top]:
        add(c)
    for lo, hi in buckets:                      # every entry-limit shape
        for c in ordered:
            m = c.get("max_entries_per_user")
            if isinstance(m, int) and lo <= m <= hi:
                add(c)
                break
    by_field = sorted((c for c in ordered if c.get("entries")),
                      key=lambda c: (int(c["entries"]), int(c["id"])))
    if by_field:                                # the extremes of field size
        add(by_field[0])
        add(by_field[-1])
    return picked


def _write(name, payload, source, records, capdir=CAP):
    os.makedirs(capdir, exist_ok=True)
    bad = _scan(payload)
    if bad:
        raise RuntimeError(f"refusing to write {name}: credential-shaped keys {bad}")
    path = os.path.join(capdir, name)
    with open(path, "w") as fh:
        json.dump(payload, fh, sort_keys=True)
    sha, n = _sha(path)
    return {"file": name, "sha256": sha, "bytes": n, "records": int(records),
            "source": source, "retrieved_ts": int(time.time()),
            "normalization_version": NORM_VERSION}


def capture(dgs=DGS, capdir=CAP, log=print, merge=False, contest_ids=()):
    """Fetch once, store, hash. The only function in S6 that touches DK.

    `merge=True` keeps the manifest's other draft groups (and their `added`
    records) instead of rewriting the manifest from scratch; `contest_ids`
    names details to fetch explicitly. Both exist for S7, which pinned the
    DET @ BUF group AFTER its game, when the lobby no longer listed its
    contests but the draftables and the contest detail were still served."""
    import dk
    man = (manifest(capdir) if merge and os.path.exists(os.path.join(capdir, "manifest.json"))
           else {"normalization_version": NORM_VERSION, "draft_groups": {}})
    for dg in dgs:
        slate = dk.slate_for("nfl", draft_group_id=int(dg))
        if not slate:
            raise SystemExit(f"draft group {dg} returned no slate")
        ent = {"slate": _write(f"slate_{dg}.json", slate, f"dk.slate_for(nfl, {dg})",
                               slate.get("n_players") or 0, capdir)}
        rows = dk.contests("nfl", draft_group_id=int(dg)) or []
        details = {}
        for cid in list(select(rows)) + [int(c) for c in contest_ids]:
            if str(cid) in details:
                continue
            d = dk.contest_detail(cid)
            if d and int(d.get("draft_group_id") or dg) == int(dg):
                details[str(cid)] = d
        ent["contests"] = _write(f"contests_{dg}.json",
                                 {"lobby": rows, "detail": details},
                                 f"dk.contests + dk.contest_detail (nfl, {dg})",
                                 len(details), capdir)
        man["draft_groups"][str(dg)] = ent
        log(f"[S6C] dg {dg}: slate {ent['slate']['bytes']:,}B "
            f"({ent['slate']['records']} players), {len(details)} contests detailed")
    with open(os.path.join(capdir, "manifest.json"), "w") as fh:
        json.dump(man, fh, indent=1, sort_keys=True)
    return man


def capture_detail(dg, cid, capdir=CAP, log=print):
    """Add ONE contest's full detail to an existing capture, and re-hash.

    Why this exists: the first capture's select() covered every entry-limit
    bucket and both field-size extremes but ranked by prize pool, so all 27
    details it took share one ladder topology (steep top, flat tail). On draft
    group 153086 alone, 401 of the 881 lobby rows are Double Ups, Satellites,
    Winner-Take-Alls, Beginner or Qualifier contests, and NONE was captured.
    For a stage whose stated risk is one contest getting another's payout
    ladder, the corpus needs a second ladder shape. The lobby listing cannot be
    re-fetched for a played draft group, so this appends to the file already
    on disk rather than re-running capture().

    The file's sha256 changes, and the manifest is rewritten to say so, with
    the added id and when it was added kept beside the original retrieval
    stamp -- a capture that quietly changed under its own hash is exactly what
    the manifest exists to prevent."""
    import dk
    man = manifest(capdir)
    ent = man["draft_groups"][str(dg)]
    rec = ent["contests"]
    path = os.path.join(capdir, rec["file"])
    with open(path) as fh:
        blob = json.load(fh)
    if str(cid) in (blob.get("detail") or {}):
        log(f"[S6C] dg {dg}: contest {cid} already captured")
        return rec
    d = dk.contest_detail(int(cid))
    if not d:
        raise SystemExit(f"contest {cid} returned no detail")
    lobby_ids = {str(r.get("id")) for r in blob.get("lobby") or []}
    if str(cid) not in lobby_ids:
        raise SystemExit(f"contest {cid} is not on the captured lobby for dg {dg}")
    blob.setdefault("detail", {})[str(cid)] = d
    bad = _scan(blob)
    if bad:
        raise RuntimeError(f"refusing to write {rec['file']}: credential-shaped keys {bad}")
    with open(path, "w") as fh:
        json.dump(blob, fh, sort_keys=True)
    sha, n = _sha(path)
    rec.update({"sha256": sha, "bytes": n, "records": len(blob["detail"])})
    rec.setdefault("added", []).append({"id": int(cid), "ts": int(time.time()),
                                        "kind": d.get("kind"), "name": d.get("name")})
    with open(os.path.join(capdir, "manifest.json"), "w") as fh:
        json.dump(man, fh, indent=1, sort_keys=True)
    log(f"[S6C] dg {dg}: added {cid} ({d.get('kind')}, {d.get('name')}); "
        f"{rec['records']} details, sha {sha[:12]}")
    return rec


# ---- the offline side ------------------------------------------------------
def manifest(capdir=CAP):
    with open(os.path.join(capdir, "manifest.json")) as fh:
        return json.load(fh)


def verify(capdir=CAP):
    """Every captured file still hashes to what the manifest recorded."""
    man = manifest(capdir)
    out = {}
    for dg, ent in man["draft_groups"].items():
        for kind, rec in ent.items():
            sha, n = _sha(os.path.join(capdir, rec["file"]))
            out[rec["file"]] = {"expected": rec["sha256"], "actual": sha,
                                "match": sha == rec["sha256"], "bytes": n}
    return out


def replay(dg, capdir=CAP):
    """(slate, {contest_id: detail}) from the capture. No network."""
    man = manifest(capdir)
    ent = man["draft_groups"][str(dg)]
    with open(os.path.join(capdir, ent["slate"]["file"])) as fh:
        slate = json.load(fh)
    with open(os.path.join(capdir, ent["contests"]["file"])) as fh:
        blob = json.load(fh)
    return slate, blob.get("detail") or {}


def dk_hashes(dg, capdir=CAP):
    """The DraftKings provenance block. Deliberately NOT merged with the Sleeper
    block: one source being pinned says nothing about the other."""
    ent = manifest(capdir)["draft_groups"][str(dg)]
    return {"source": "draftkings", "normalization_version": NORM_VERSION,
            "files": {k: {"file": v["file"], "sha256": v["sha256"],
                          "bytes": v["bytes"], "records": v["records"],
                          "retrieved_ts": v["retrieved_ts"], "endpoint": v["source"]}
                      for k, v in ent.items()}}


def sleeper_hashes(feed_dir=None):
    """The Sleeper provenance block, separately."""
    feed_dir = feed_dir or os.path.join(DATA, "feeds")
    out = {}
    for fn in sorted(os.listdir(feed_dir)):
        if not fn.endswith(".json"):
            continue
        sha, n = _sha(os.path.join(feed_dir, fn))
        out[fn] = {"sha256": sha, "bytes": n}
    return {"source": "sleeper", "files": out}


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "verify":
        bad = {k: v for k, v in verify().items() if not v["match"]}
        print("capture verify:", "clean" if not bad else f"MISMATCH {bad}")
        raise SystemExit(1 if bad else 0)
    if args and args[0] == "add":
        capture_detail(int(args[1]), int(args[2]))
        raise SystemExit(0)
    capture(tuple(int(x) for x in args) or DGS)
    print("DK inputs captured")
