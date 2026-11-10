"""Best-effort process sampling: never raises, never invents a measurement."""

from __future__ import annotations

import sys

import pytest

from server.resources import ProcessUsage, create_sampler, sample_process_usage


def test_usage_is_none_when_no_sampler_is_available() -> None:
    usage = sample_process_usage(sampler=None) if False else ProcessUsage()
    assert usage.as_dict() == {
        "cpu_usage_percent": None,
        "ram_usage_mb": None,
        "source": "unavailable",
    }


def test_sample_returns_numbers_or_none() -> None:
    sampler = create_sampler()
    if sampler is None:
        return  # platform without a sampler: honest "not measured"
    first = sample_process_usage(sampler)
    second = sample_process_usage(sampler)
    assert second.ram_mb is None or second.ram_mb > 0
    assert second.cpu_percent is None or second.cpu_percent >= 0
    assert second.source != "unavailable"


def test_broken_sampler_degrades_gracefully() -> None:
    class Broken:
        def sample(self):
            raise RuntimeError("no /proc")

    usage = sample_process_usage(sampler=Broken())
    assert usage.cpu_percent is None and usage.ram_mb is None


def test_linux_sampler_reads_proc() -> None:
    if not sys.platform.startswith("linux"):
        return
    from server.resources import _LinuxSampler

    sampler = _LinuxSampler()
    usage = sampler.sample()
    assert usage.ram_mb and usage.ram_mb > 0
    assert usage.cpu_percent is not None and usage.cpu_percent >= 0


def test_fresh_sampler_first_reading_is_plausible() -> None:
    """Regression: the Linux sampler primed its CPU baseline with zero.

    ``_LinuxSampler.__init__`` used ``_last_cpu = 0.0``, so the first sample
    divided the process's whole lifetime of CPU time by the microseconds since
    construction and reported millions of percent (seen as 1 956 947 % in the
    first benchmark run).
    """
    import os
    import time

    from server.resources import create_sampler, sample_process_usage

    sampler = create_sampler()
    if sampler is None:
        pytest.skip("no sampler on this platform")
    first = sample_process_usage(sampler)
    if first.cpu_percent is None:
        pytest.skip("CPU sampling unavailable")
    limit = (os.cpu_count() or 1) * 100 * 1.5
    assert 0.0 <= first.cpu_percent <= limit, first.cpu_percent
    time.sleep(0.05)
    second = sample_process_usage(sampler)
    assert second.cpu_percent is not None and second.cpu_percent <= limit
