"""What a build really holds in memory, measured while it runs (2026-11-27).

The build profiles size themselves from a model (pc_profiles.memory_gb) fitted on a synthetic slate here
(research/hp_memory.py). The owner: "verify both on your PC". A `Watch` samples, once a second on a daemon
thread, this process's resident memory and the machine's memory in use, and keeps the peaks; the PC worker
wraps every tournament build in one and stamps the result on the board beside the estimate
(pc_worker._mem_report). Standard library only (ctypes on Windows, /proc on Linux): no psutil on the PC.

The machine's figure is the headline: it counts the scoring pool's worker processes and the shared-memory
copies they map, which a per-process figure would count once per worker or not at all, and everything else
the PC is doing (the owner leaves it unattended, so mostly the build). The process figure is THIS process
only -- the build's pools are child processes and are not in it.

Two Windows readings were wrong on the PC's deep probe (2026-11-27) and are fixed here:
  process_rss read None on every sample: GetCurrentProcess returns a pseudo-handle (-1, a 64-bit HANDLE),
      and without restype ctypes took it as a 32-bit int and passed it on truncated, so
      GetProcessMemoryInfo failed. Both functions now declare their types (_win_process_rss).
  system_used read a total of 347.76 GB and 0.0 in use for two hours of one run (31.78 GB, correctly, in
      the other two, the same code): every reading is now checked -- in use above 0 and at most the total,
      and the total within 2% of this process's first good reading -- against a second reader
      (GetPerformanceInfo) when the first fails, and when both fail the sample is None and counted
      (ANOMALIES), so a report says "unreadable" instead of a wrong figure.
"""
import os
import threading
import time

_GB = 1024.0 ** 3
TOTAL_TOL = 0.02                         # the machine's total may not move more than this between readings
ANOMALIES = {"n": 0, "last": None}       # samples no reader could give sanely, this process
_FIRST_TOTAL = [None]


def _win_process_rss(k32, psapi):
    """WorkingSetSize of this process through the Win32 API objects given (ctypes.windll's, or a guard's
    stand-ins), with every type declared: the pseudo-handle is a 64-bit HANDLE, never a 32-bit int."""
    import ctypes
    from ctypes import wintypes

    class _PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    k32.GetCurrentProcess.argtypes = []
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    pmc = _PMC()
    pmc.cb = ctypes.sizeof(_PMC)
    h = k32.GetCurrentProcess()
    if psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
        return int(pmc.WorkingSetSize)
    return None


def process_rss():
    """This process's resident memory in bytes, or None (the build's pool workers are not in it)."""
    try:
        if os.name == "nt":
            import ctypes
            return _win_process_rss(ctypes.windll.kernel32, ctypes.windll.psapi)
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError, IndexError, TypeError):
        return None


def _win_global_status():
    """(in use, total) bytes from GlobalMemoryStatusEx."""
    import ctypes
    from ctypes import wintypes

    class _MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    k32 = ctypes.windll.kernel32
    k32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MS)]
    k32.GlobalMemoryStatusEx.restype = wintypes.BOOL
    ms = _MS()
    ms.dwLength = ctypes.sizeof(_MS)
    if k32.GlobalMemoryStatusEx(ctypes.byref(ms)):
        return int(ms.ullTotalPhys - ms.ullAvailPhys), int(ms.ullTotalPhys)
    return None, None


def _win_performance_info():
    """(in use, total) bytes from psapi's GetPerformanceInfo (pages x PageSize): the second reader."""
    import ctypes
    from ctypes import wintypes

    class _PI(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("CommitTotal", ctypes.c_size_t), ("CommitLimit", ctypes.c_size_t),
                    ("CommitPeak", ctypes.c_size_t), ("PhysicalTotal", ctypes.c_size_t),
                    ("PhysicalAvailable", ctypes.c_size_t), ("SystemCache", ctypes.c_size_t),
                    ("KernelTotal", ctypes.c_size_t), ("KernelPaged", ctypes.c_size_t),
                    ("KernelNonpaged", ctypes.c_size_t), ("PageSize", ctypes.c_size_t),
                    ("HandleCount", wintypes.DWORD), ("ProcessCount", wintypes.DWORD),
                    ("ThreadCount", wintypes.DWORD)]
    psapi = ctypes.windll.psapi
    psapi.GetPerformanceInfo.argtypes = [ctypes.POINTER(_PI), wintypes.DWORD]
    psapi.GetPerformanceInfo.restype = wintypes.BOOL
    pi = _PI()
    pi.cb = ctypes.sizeof(_PI)
    if psapi.GetPerformanceInfo(ctypes.byref(pi), pi.cb):
        page = int(pi.PageSize)
        return int(pi.PhysicalTotal - pi.PhysicalAvailable) * page, int(pi.PhysicalTotal) * page
    return None, None


def _proc_meminfo():
    info = {}
    with open("/proc/meminfo") as fh:
        for line in fh:
            k, v = line.split(":", 1)
            info[k] = int(v.split()[0]) * 1024
    return info["MemTotal"] - info["MemAvailable"], info["MemTotal"]


def _readers():
    return (_win_global_status, _win_performance_info) if os.name == "nt" else (_proc_meminfo,)


def sane(used, total, first=None):
    """Whether a reading can be believed: in use above 0 and at most the total, and the total within
    TOTAL_TOL of the first good reading (`first`, this process's)."""
    try:
        used, total = float(used), float(total)
    except (TypeError, ValueError):
        return False
    if not (total > 0 and 0 < used <= total):
        return False
    return first is None or abs(total - first) <= TOTAL_TOL * first


def system_used(readers=None):
    """(bytes of physical memory in use machine-wide, total bytes), or (None, None) when no reader gives
    a sane reading (`sane`; each such sample counted in ANOMALIES). `readers`: (in use, total) callables,
    tried in order -- the platform's own when None."""
    for rd in (readers or _readers()):
        try:
            used, total = rd()
        except (OSError, ValueError, KeyError, AttributeError, IndexError, TypeError):
            continue
        if sane(used, total, _FIRST_TOTAL[0]):
            if _FIRST_TOTAL[0] is None:
                _FIRST_TOTAL[0] = float(total)
            return int(used), int(total)
    ANOMALIES["n"] += 1
    ANOMALIES["last"] = time.time()
    return None, None


def _reset():
    """A guard's fresh process: no first total, no anomalies."""
    _FIRST_TOTAL[0] = None
    ANOMALIES.update(n=0, last=None)


class Watch:
    """Samples from construction until stop(); `result()` is the peaks in GB (None where unreadable)."""

    def __init__(self, every=1.0):
        self.every = float(every)
        self.t0 = time.time()
        self.anom0 = ANOMALIES["n"]
        self.base_sys, self.total = system_used()
        self.base_proc = process_rss()
        self.peak_sys, self.peak_proc = self.base_sys, self.base_proc
        self._stop = threading.Event()
        self._th = threading.Thread(target=self._run, daemon=True)
        self._th.start()

    def _sample(self):
        s, t = system_used()
        p = process_rss()
        if s is not None:
            self.peak_sys = s if self.peak_sys is None else max(self.peak_sys, s)
            self.total = self.total or t
        if p is not None:
            self.peak_proc = p if self.peak_proc is None else max(self.peak_proc, p)

    def _run(self):
        while not self._stop.wait(self.every):
            self._sample()

    def stop(self):
        if not self._stop.is_set():
            self._sample()
            self._stop.set()
            self._th.join(timeout=5)
        return self.result()

    def result(self):
        g = lambda b: round(b / _GB, 2) if b is not None else None
        return {"process_peak_gb": g(self.peak_proc), "process_start_gb": g(self.base_proc),
                "system_peak_used_gb": g(self.peak_sys), "system_start_used_gb": g(self.base_sys),
                "build_added_gb": g(self.peak_sys - self.base_sys) if None not in (self.peak_sys, self.base_sys) else None,
                "system_total_gb": g(self.total), "seconds": round(time.time() - self.t0, 1),
                "sample_s": self.every, "system_unreadable_samples": ANOMALIES["n"] - self.anom0,
                "process_scope": "this process only; the build's pool workers are child processes"}
