"""Guards for the audit's record/replay harness (research/den_pit_audit/netreplay.py,
2026-11-27; the DEN @ PIT showdown audit, research/reports/den_pit_showdown_audit.md).

The audit compares two commits' boards on identical inputs, and its first recording
was wrong in two ways that changed a board without a single error: it keyed a
request on the URL alone, so nfl_dfs_sim._get's gzip answer for Sleeper's week was
handed to racing._get_json (no gzip) and the drive model's level vanished (DEN 26.0
v PIT 16.7 instead of 21.4 v 17.2), and it answered a retry after a 429 with the same
429, so a retry that succeeds in production failed under replay. What each check
proves:

  * RPL1 the same URL with a different Accept-Encoding (or User-Agent) is a different
    request, with its own answer;
  * RPL2 a key keeps a SEQUENCE: replay gives the k-th call the k-th recorded answer
    (429, 429, 200 replays as 429, 429, 200 -- an HTTPError each time, then the body);
  * RPL3 past the end of a sequence the last answer is served and logged "beyond"; a
    key never recorded is fetched live and logged "miss";
  * RPL4 install() replaces urllib.request.urlopen and nothing else.

Hermetic: the network is a stub; the store is a scratch directory.

    python3 tests/guards_auto_netreplay.py       # exits 1 on any failed check
"""
import io
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Fake:
    def __init__(self, body, status=200, headers=()):
        self._b, self.status = body, status
        self.headers = {"Content-Type": "application/json", **dict(headers)}

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def run(ck):
    here = os.path.join(ROOT, "research", "den_pit_audit")
    sys.path.insert(0, here)
    real = urllib.request.urlopen
    try:
        import netreplay as NR
    finally:
        sys.path.remove(here)            # the audit's folder never shadows a module of the tree
    saved = NR._REAL
    calls = []
    script = {}

    def fake(req, *a, **k):
        url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
        enc = req.get_header("Accept-encoding") if isinstance(req, urllib.request.Request) else None
        calls.append((url, enc))
        seq = script.setdefault((url, enc), [])
        item = seq.pop(0) if seq else (200, b"late")
        if item[0] != 200:
            raise urllib.error.HTTPError(url, item[0], "Too Many Requests", {}, io.BytesIO(b"slow down"))
        return _Fake(item[1], headers=(("Content-Encoding", "gzip"),) if enc == "gzip" else ())

    try:
        NR._REAL = fake
        with tempfile.TemporaryDirectory() as d:
            store = os.path.join(d, "net")
            u = "https://example.invalid/projections/nfl/2026/12"
            script[(u, "gzip")] = [(200, b"GZ")]
            script[(u, None)] = [(200, b"PLAIN")]
            script[("https://example.invalid/markets", None)] = [(429, b""), (429, b""), (200, b"OK")]
            NR._STATE["seq"] = {}
            NR._STATE["counts"] = {"live": 0, "hit": 0, "miss": 0, "beyond": 0}
            NR.install(store, "record", os.path.join(d, "rec.jsonl"))
            ck("RPL4 install() replaces urllib.request.urlopen (and only that)",
               urllib.request.urlopen is NR.urlopen and NR._REAL is fake)

            def get(url, enc=None):
                h = {"User-Agent": "x"}
                if enc:
                    h["Accept-Encoding"] = enc
                try:
                    with urllib.request.urlopen(urllib.request.Request(url, headers=h)) as r:
                        return r.read()
                except urllib.error.HTTPError as e:
                    return ("HTTP", e.code)

            rec = [get(u, "gzip"), get(u), get("https://example.invalid/markets"),
                   get("https://example.invalid/markets"), get("https://example.invalid/markets")]
            n_live = len(calls)
            NR._STATE["seq"] = {}
            NR.install(store, "replay", os.path.join(d, "rep.jsonl"))
            rep = [get(u, "gzip"), get(u), get("https://example.invalid/markets"),
                   get("https://example.invalid/markets"), get("https://example.invalid/markets")]
            ck("RPL1 the same URL with another Accept-Encoding is its own request with its own answer",
               rec[0] == b"GZ" and rec[1] == b"PLAIN" and rep[0] == b"GZ" and rep[1] == b"PLAIN", f"{rec} {rep}")
            ck("RPL2 a retried key replays its recorded sequence (429, 429, 200), never the first answer again",
               rec[2:] == [("HTTP", 429), ("HTTP", 429), b"OK"] and rep[2:] == rec[2:] and len(calls) == n_live,
               f"{rec[2:]} {rep[2:]} live calls {len(calls)} vs {n_live}")
            extra = get("https://example.invalid/markets")
            script[("https://example.invalid/new", None)] = [(200, b"NEW")]
            new = get("https://example.invalid/new")
            log = [json.loads(x) for x in open(os.path.join(d, "rep.jsonl"))]
            ck("RPL3 past a sequence's end the last answer is served ('beyond'); an unrecorded key is fetched live "
               "('miss')", extra == b"OK" and new == b"NEW" and [x["how"] for x in log[-2:]] == ["beyond", "miss"],
               f"{extra} {new} {[x['how'] for x in log[-2:]]}")
    finally:
        NR._REAL = saved
        urllib.request.urlopen = real


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
