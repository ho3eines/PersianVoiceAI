"""Best-effort local CPU/RAM sampling without adding a dependency.

``psutil`` is used when it happens to be installed; otherwise Linux reads
``/proc``, Windows uses ``psapi`` through ``ctypes`` and anything else returns
an empty mapping.  Nothing here is required for the assistant to work: a
missing measurement is reported as *not measured*, never as zero.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes  # noqa: F401  (only used on Windows)
import os
import sys
import time
from dataclasses import dataclass


@dataclass(slots=True)
class ProcessUsage:
    cpu_percent: float | None = None
    ram_mb: float | None = None
    source: str = "unavailable"

    def as_dict(self) -> dict[str, float | str | None]:
        return {
            "cpu_usage_percent": self.cpu_percent,
            "ram_usage_mb": self.ram_mb,
            "source": self.source,
        }


class _PsutilSampler:
    source = "psutil"

    def __init__(self) -> None:
        import psutil  # optional dependency

        self._process = psutil.Process()
        self._process.cpu_percent(None)  # prime the delta measurement
        self._primed_at = time.monotonic()

    def sample(self) -> ProcessUsage:
        cpu = float(self._process.cpu_percent(None))
        ram = float(self._process.memory_info().rss) / (1024 * 1024)
        return ProcessUsage(round(cpu, 1), round(ram, 1), self.source)


class _LinuxSampler:
    source = "proc"

    def __init__(self) -> None:
        self._clock_ticks = os.sysconf("SC_CLK_TCK")
        self._page_size = os.sysconf("SC_PAGE_SIZE")
        # Prime the baseline with the *current* tick count.  Starting from 0
        # made the first sample report the process's entire lifetime of CPU
        # time divided by a few microseconds — millions of percent.
        self._last_cpu = self._total_ticks()
        self._last_at = time.monotonic()

    def _total_ticks(self) -> float:
        with open("/proc/self/stat", encoding="utf-8") as handle:  # noqa: PTH123
            fields = handle.read().split()
        # utime + stime are fields 14 and 15 (1-based).
        return float(fields[13]) + float(fields[14])

    def _rss_bytes(self) -> int:
        with open("/proc/self/statm", encoding="utf-8") as handle:  # noqa: PTH123
            resident = int(handle.read().split()[1])
        return resident * self._page_size

    def sample(self) -> ProcessUsage:
        now = time.monotonic()
        ticks = self._total_ticks()
        elapsed = max(1e-6, now - self._last_at)
        cpu = (ticks - self._last_cpu) / self._clock_ticks / elapsed * 100.0
        self._last_cpu, self._last_at = ticks, now
        return ProcessUsage(round(cpu, 1), round(self._rss_bytes() / (1024 * 1024), 1), self.source)


class _WindowsSampler:
    source = "psapi"

    class _Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    def __init__(self) -> None:
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._psapi = ctypes.WinDLL("psapi", use_last_error=True)
        self._last_wall = time.perf_counter()
        self._last_cpu = sum(self._times())
        self._handle = self._kernel32.GetCurrentProcess()

    def _times(self) -> tuple[float, float, float, float]:
        times = wintypes.FILETIME * 4  # type: ignore[operator]
        values = times()
        self._kernel32.GetProcessTimes(
            self._handle, *[ctypes.byref(value) for value in values]
        )
        return tuple(
            (value.dwHighDateTime << 32 | value.dwLowDateTime) / 1e7 for value in values
        )

    def sample(self) -> ProcessUsage:
        wall = time.perf_counter()
        cpu_times = sum(self._times())
        elapsed = max(1e-6, wall - self._last_wall)
        cpu = (cpu_times - self._last_cpu) / elapsed * 100.0
        self._last_cpu, self._last_wall = cpu_times, wall
        counters = self._Counters()
        counters.cb = ctypes.sizeof(counters)
        ram = None
        if self._psapi.GetProcessMemoryInfo(
            self._handle, ctypes.byref(counters), counters.cb
        ):
            ram = round(counters.WorkingSetSize / (1024 * 1024), 1)
        return ProcessUsage(round(cpu, 1), ram, self.source)


def create_sampler() -> object | None:
    """Return a sampler for the current platform, or ``None`` when unavailable."""
    try:
        return _PsutilSampler()
    except Exception:
        pass
    try:
        if sys.platform.startswith("linux"):
            return _LinuxSampler()
        if os.name == "nt":
            return _WindowsSampler()
    except Exception:
        return None
    return None


def sample_process_usage(sampler: object | None = None) -> ProcessUsage:
    """Return the current process usage, or an all-``None`` reading."""
    active = sampler if sampler is not None else create_sampler()
    if active is None:
        return ProcessUsage()
    try:
        return active.sample()  # type: ignore[attr-defined]
    except Exception:
        return ProcessUsage()
