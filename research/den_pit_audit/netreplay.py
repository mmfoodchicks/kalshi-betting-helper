"""Record every HTTP response, replay it byte for byte, in order (research only).

The DEN @ PIT showdown audit (research/reports/den_pit_showdown_audit.md)
builds the same board on two commits. Sleeper, DraftKings, ESPN and Kalshi
all move between two calls minutes apart, so a comparison that fetched twice
would mix the sprint's effect with the inputs'. Every module in this tree
fetches through urllib.request.urlopen, so wrapping that one function before
anything else is imported captures every input.

A request is keyed by sha256(method, url, body, Accept-Encoding, User-Agent):
two modules that read the same URL with different headers get different
answers (nfl_dfs_sim._get asks Sleeper for gzip and decompresses it,
racing._get_json does not; one shared answer broke the second reader), and a
retry with another User-Agent is its own request. Each key keeps a SEQUENCE:

    VIGIL_NET_MODE=record  -- every call goes to the network; the k-th answer
                              for a key is stored as <key>.<k>
    VIGIL_NET_MODE=replay  -- the k-th call for a key gets the k-th stored
                              answer; past the end of the sequence the LAST
                              answer is served (logged "beyond"); a key never
                              recorded is fetched live and logged "miss"

so a retry after a 429 replays as the same 429-then-200 the recording saw.
HTTP errors are stored and raised again as HTTPError; transport errors as
URLError with their message. The request log (one JSON line per call) is the
evidence that two builds read the same inputs.
"""
import email
import email.message
import hashlib
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request

_REAL = urllib.request.urlopen
_LOCK = threading.Lock()
_STATE = {"dir": None, "mode": None, "log": None, "seq": {},
          "counts": {"live": 0, "hit": 0, "miss": 0, "beyond": 0}}


class _Headers(email.message.Message):
    pass


class _Resp(io.BytesIO):
    """Enough of http.client.HTTPResponse for this tree's readers."""

    def __init__(self, body, status, headers, url):
        super().__init__(body)
        self.status = self.code = int(status)
        self.reason = "OK"
        self.url = url
        self.headers = self.msg = email.message_from_string(
            "".join(f"{k}: {v}\n" for k, v in headers) + "\n", _class=_Headers)

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url

    def info(self):
        return self.headers

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def getheaders(self):
        return list(self.headers.items())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def _hdr(req, name):
    if not isinstance(req, urllib.request.Request):
        return ""
    for k, v in req.header_items():
        if k.lower() == name.lower():
            return str(v)
    return ""


def _key(req):
    if isinstance(req, urllib.request.Request):
        method, url = req.get_method(), req.full_url
        data = req.data if isinstance(req.data, bytes) else b""
    else:
        method, url, data = "GET", str(req), b""
    h = hashlib.sha256()
    for part in (method, url, _hdr(req, "Accept-Encoding"), _hdr(req, "User-Agent")):
        h.update(part.encode())
        h.update(b"\0")
    h.update(data or b"")
    return h.hexdigest()[:32], url


def _log(entry):
    if _STATE["log"]:
        with _LOCK:
            with open(_STATE["log"], "a") as fh:
                fh.write(json.dumps(entry) + "\n")


def _path(k, n, ext):
    return os.path.join(_STATE["dir"], f"{k}.{n}.{ext}")


def _from_store(k, n, url):
    with open(_path(k, n, "json")) as fh:
        meta = json.load(fh)
    body = b""
    if os.path.exists(_path(k, n, "bin")):
        with open(_path(k, n, "bin"), "rb") as fh:
            body = fh.read()
    if meta.get("transport_error"):
        raise urllib.error.URLError(meta["transport_error"])
    if meta.get("http_error"):
        raise urllib.error.HTTPError(url, meta["status"], meta.get("reason") or "",
                                     _Resp(b"", 200, meta.get("headers") or [], url).headers, io.BytesIO(body))
    return _Resp(body, meta.get("status") or 200, meta.get("headers") or [], url)


def _fetch_and_store(req, k, n, url, args, kwargs):
    meta = {"url": url, "t": time.time(), "n": n}
    body = b""
    try:
        with _REAL(req, *args, **kwargs) as resp:
            body = resp.read()
            meta.update(status=getattr(resp, "status", 200), headers=list(resp.headers.items()))
    except urllib.error.HTTPError as e:
        try:
            body = e.read() or b""
        except Exception:
            body = b""
        meta.update(http_error=True, status=e.code, reason=str(e.reason),
                    headers=list(e.headers.items()) if e.headers else [])
    except Exception as e:                     # timeouts, refusals: stored as what they were
        meta.update(transport_error=f"{type(e).__name__}: {e}")
    with open(_path(k, n, "bin"), "wb") as fh:
        fh.write(body)
    with open(_path(k, n, "json"), "w") as fh:
        json.dump(meta, fh)
    return _from_store(k, n, url)


def urlopen(req, *args, **kwargs):
    k, url = _key(req)
    with _LOCK:
        n = _STATE["seq"].get(k, 0)
        _STATE["seq"][k] = n + 1
    if _STATE["mode"] == "record":
        how = "live"
        use = n
    elif os.path.exists(_path(k, n, "json")):
        how, use = "hit", n
    else:
        m = n
        while m > 0 and not os.path.exists(_path(k, m - 1, "json")):
            m -= 1
        if m > 0:
            how, use = "beyond", m - 1
        else:
            how, use = "miss", 0
    with _LOCK:
        _STATE["counts"][how] += 1
    _log({"k": k, "n": n, "url": url, "how": how})
    if how in ("live", "miss"):
        return _fetch_and_store(req, k, use, url, args, kwargs)
    return _from_store(k, use, url)


def install(store_dir, mode, log_path=None):
    """Wrap urllib.request.urlopen for this process (call before importing
    any module of the tree: `from urllib.request import urlopen` binds early)."""
    os.makedirs(store_dir, exist_ok=True)
    _STATE.update(dir=store_dir, mode=mode, log=log_path)
    urllib.request.urlopen = urlopen
    return _STATE


def counts():
    return dict(_STATE["counts"])
