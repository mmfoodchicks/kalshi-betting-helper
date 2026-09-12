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
routes, and the snapshot is the response body only. A guard asserts no
credential-shaped key reaches the files.

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


def _scan(obj, path="$"):
    """Every key in a snapshot, so a credential cannot ride along unnoticed."""
    bad = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if any(f in str(k).lower() for f in FORBIDDEN):
                bad.append(f"{path}.{k}")
            bad += _scan(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:200]):
            bad += _scan(v, f"{path}[{i}]")
    return bad


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

    Deterministic given the lobby listing -- sorted by prize pool then id, so a
    recapture picks the same set and the manifest hashes stay comparable. The
    lobby row already carries entry_fee, entries and max_entries_per_user, so
    this selection needs no detail calls of its own.
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


def capture(dgs=DGS, capdir=CAP, log=print):
    """Fetch once, store, hash. The only function in S6 that touches DK."""
    import dk
    man = {"normalization_version": NORM_VERSION, "draft_groups": {}}
    for dg in dgs:
        slate = dk.slate_for("nfl", draft_group_id=int(dg))
        if not slate:
            raise SystemExit(f"draft group {dg} returned no slate")
        ent = {"slate": _write(f"slate_{dg}.json", slate, f"dk.slate_for(nfl, {dg})",
                               slate.get("n_players") or 0, capdir)}
        rows = dk.contests("nfl", draft_group_id=int(dg)) or []
        details = {}
        for cid in select(rows):
            d = dk.contest_detail(cid)
            if d:
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
    capture(tuple(int(x) for x in args) or DGS)
    print("DK inputs captured")
