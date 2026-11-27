"""What a build really holds in memory, measured while it runs (2026-11-27).

The build profiles size themselves from a model (pc_profiles.memory_gb) fitted on a synthetic slate here
(research/hp_memory.py). The owner: "verify both on your PC". A `Watch` samples, once a second on a daemon
thread, this process's resident memory and the machine's memory in use, and keeps the peaks; the PC worker
wraps every tournament build in one and stamps the result on the board beside the estimate
(pc_worker._mem_report). Standard library only (ctypes on Windows, /proc on Linux): no psutil on the PC.

The machine's figure is what decides whether a build fits: it counts the scoring pool's worker processes
and the shared-memory copies they map, which a per-process figure would count once per worker or not at
all, and everything else the PC is doing (the owner leaves it unattended, so mostly the build).
"""
import os
import threading
import time

_GB = 1024.0 ** 3


def process_rss():
    """This process's resident memory in bytes, or None."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class _PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            pmc = _PMC()
            pmc.cb = ctypes.sizeof(_PMC)
            h = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                return int(pmc.WorkingSetSize)
            return None
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError, IndexError):
        return None


def system_used():
    """(bytes of physical memory in use machine-wide, total bytes), or (None, None)."""
    try:
        if os.name == "nt":
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = _MS()
            ms.dwLength = ctypes.sizeof(_MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
                return int(ms.ullTotalPhys - ms.ullAvailPhys), int(ms.ullTotalPhys)
            return None, None
        info = {}
        with open("/proc/meminfo") as fh:
            for line in fh:
                k, v = line.split(":", 1)
                info[k] = int(v.split()[0]) * 1024
        return info["MemTotal"] - info["MemAvailable"], info["MemTotal"]
    except (OSError, ValueError, KeyError, AttributeError, IndexError):
        return None, None


class Watch:
    """Samples from construction until stop(); `result()` is the peaks in GB (None where unreadable)."""

    def __init__(self, every=1.0):
        self.every = float(every)
        self.t0 = time.time()
        self.base_sys, self.total = system_used()
        self.base_proc = process_rss()
        self.peak_sys, self.peak_proc = self.base_sys, self.base_proc
        self._stop = threading.Event()
        self._th = threading.Thread(target=self._run, daemon=True)
        self._th.start()

    def _sample(self):
        s, _t = system_used()
        p = process_rss()
        if s is not None:
            self.peak_sys = s if self.peak_sys is None else max(self.peak_sys, s)
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
                "sample_s": self.every}
